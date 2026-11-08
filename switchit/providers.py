"""Provider adapters and capability descriptions.

Adapter kinds:

- ``recorded``   — offline baseline adapter that replays the historical
  response captured in the case. This is the "current provider" side of a
  comparison and never contacts anything.
- ``mock``       — offline, deterministic target adapter for the complete
  offline replay loop and for tests.
- ``openai``     — live OpenAI-compatible HTTP adapter (also local vLLM).
- ``anthropic``  — live Anthropic Messages adapter.
- ``bedrock``    — live Amazon Bedrock Converse adapter (requires boto3).

Remote safety rules:

- Live adapters are inert unless constructed with ``remote=True``, which the
  CLI only sets from the explicit ``--remote`` flag. Local import, mapping,
  case selection, and offline replay never construct a remote adapter.
- Credentials come from environment variables only and are never written to
  files, results, or logs.
- Adapters capture proposed tool calls. Nothing in this module executes a
  business tool, ever.

Capability descriptions are declarations to be checked against provider
documentation, not verified measurements.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

USER_AGENT = "switchit-harness/0.1"
DEFAULT_TIMEOUT_SECONDS = 60
ANTHROPIC_DEFAULT_MAX_TOKENS = 1024


class ProviderError(Exception):
    """Raised for any provider-side failure, including configuration gaps."""


class RemoteDisabledError(ProviderError):
    """Raised when a live adapter is used without the explicit remote opt-in."""


def _estimated_usage(text: str, estimator: str = "word_count") -> dict:
    """Rough local token estimate. Always labeled ``estimated``."""
    words = len(re.findall(r"\S+", text or ""))
    tokens = max(1, int(words * 1.3))
    return {
        "input_tokens": None,
        "output_tokens": tokens,
        "basis": "estimated",
        "estimator": estimator,
    }


def _unknown_usage() -> dict:
    return {"input_tokens": None, "output_tokens": None, "basis": "unknown", "estimator": None}


def _recorded_usage(input_tokens, output_tokens) -> dict:
    if input_tokens is None and output_tokens is None:
        return _unknown_usage()
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "basis": "recorded",
        "estimator": None,
    }


class ProviderAdapter:
    """Base adapter. ``kind`` is offline|live; ``remote`` gates any network use."""

    name = "base"
    kind = "offline"
    remote = False

    def capabilities(self) -> dict:
        return {
            "tool_calling": False,
            "structured_output": False,
            "streaming": False,
            "caching": False,
            "max_context_tokens": None,
            "notes": "",
        }

    def replay(self, request: dict, case: dict | None = None) -> dict:
        raise NotImplementedError

    @staticmethod
    def _result(text, tool_calls, finish_reason, error, latency_ms, usage, model=None) -> dict:
        return {
            "text": text or "",
            "tool_calls": tool_calls or [],
            "finish_reason": finish_reason,
            "error": error,
            "latency_ms": latency_ms,
            "usage": usage,
            "adapter": None,  # set by the concrete adapter
            "model": model,
        }


class RecordedBaselineAdapter(ProviderAdapter):
    """Replay the historical recorded response for a case. Fully offline.

    Usage provenance from the source is preserved verbatim: a recorded
    historical call stays recorded, an estimated one stays estimated, and a
    call with no usage stays unknown. This is the reference side of every
    comparison — it says what the current provider did, not what success is.
    """

    name = "recorded"
    kind = "offline"
    remote = False

    def __init__(self, model: str | None = None):
        self.model = model

    def capabilities(self) -> dict:
        return {
            "tool_calling": True,
            "structured_output": True,
            "streaming": False,
            "caching": False,
            "max_context_tokens": None,
            "notes": "Recorded historical responses; capabilities mirror whatever the source system used.",
        }

    def replay(self, request: dict, case: dict | None = None) -> dict:
        if not case or "historical" not in case:
            raise ProviderError(
                "The recorded adapter replays case history and requires a case with a 'historical' field."
            )
        historical = case["historical"]
        usage = dict(historical.get("usage") or {})
        usage.setdefault("basis", "unknown")
        usage.setdefault("estimator", None)
        result = self._result(
            text=historical.get("text") or "",
            tool_calls=historical.get("tool_calls") or [],
            finish_reason=historical.get("finish_reason"),
            error=historical.get("error"),
            latency_ms=historical.get("latency_ms"),
            usage=usage,
            model=historical.get("model") or self.model,
        )
        result["adapter"] = self.name
        return result


class MockAdapter(ProviderAdapter):
    """Deterministic offline target adapter.

    Modes:
    - ``mirror``  — reproduce the case's historical tool calls and text head.
      Simulates a target that behaves like the recording.
    - ``echo``    — echo the last user message; no tool calls. Simulates a
      target that lost tool-calling behavior.
    - ``canned``  — return configured text and optional configured tool calls.
    - ``degrade`` — return configured text but drop all tool calls.
    - ``fail``    — return a provider error for every case.

    Usage is a local word-count estimate, explicitly labeled ``estimated``.
    """

    name = "mock"
    kind = "offline"
    remote = False

    MODES = ("mirror", "echo", "canned", "degrade", "fail")

    def __init__(self, mode: str = "mirror", text: str | None = None,
                 tool_calls: list | None = None, model: str = "mock-model"):
        if mode not in self.MODES:
            raise ProviderError(f"Unknown mock mode {mode!r}; expected one of {', '.join(self.MODES)}")
        self.mode = mode
        self.text = text
        self.tool_calls = tool_calls or []
        self.model = model

    def capabilities(self) -> dict:
        return {
            "tool_calling": self.mode in ("mirror", "canned"),
            "structured_output": self.mode in ("mirror", "canned"),
            "streaming": False,
            "caching": False,
            "max_context_tokens": 128000,
            "notes": f"Deterministic mock adapter in '{self.mode}' mode, for offline runs only.",
        }

    def replay(self, request: dict, case: dict | None = None) -> dict:
        started = time.monotonic()
        error = None
        text = ""
        tool_calls: list = []
        finish_reason = "stop"

        if self.mode == "fail":
            error = "mock provider error: every request fails in 'fail' mode"
        elif self.mode == "mirror":
            historical = (case or {}).get("historical") or {}
            text = "MOCK MIRROR: " + (historical.get("text") or "")[:300]
            tool_calls = [
                {"name": call.get("name", ""), "arguments": dict(call.get("arguments") or {})}
                for call in historical.get("tool_calls") or []
            ]
            # Mirroring is faithful: a historical failure stays a failure so
            # error cases remain visible instead of silently "passing".
            error = historical.get("error")
            finish_reason = historical.get("finish_reason") or finish_reason
        elif self.mode == "echo":
            last_user = ""
            for message in (request or {}).get("messages") or []:
                if message.get("role") == "user" and message.get("content"):
                    last_user = message["content"]
            text = "MOCK ECHO: " + last_user[:400]
            finish_reason = "stop"
        elif self.mode in ("canned", "degrade"):
            text = self.text if self.text is not None else "MOCK: canned response"
            if self.mode == "canned":
                tool_calls = [dict(call) for call in self.tool_calls]

        latency_ms = int((time.monotonic() - started) * 1000) + 1
        usage = (
            _estimated_usage(text)
            if not error
            else _unknown_usage()
        )
        result = self._result(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            error=error,
            latency_ms=latency_ms,
            usage=usage,
            model=self.model,
        )
        result["adapter"] = self.name
        return result


# ---------------------------------------------------------------------------
# Message conversion helpers shared by the live adapters
# ---------------------------------------------------------------------------


def _arguments_to_json(arguments) -> str:
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _content_to_str(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def to_openai_messages(messages: list) -> list:
    """Map canonical messages to OpenAI chat format.

    Historical assistant tool_calls get fabricated ids (``call_N``) and the
    following tool-role messages are matched to them in order. This is a
    lossy but explicit reconstruction from telemetry that did not preserve
    provider call ids.
    """
    converted = []
    pending_calls = []
    for message in messages or []:
        role = message.get("role") or "user"
        content = _content_to_str(message.get("content"))
        if role == "assistant":
            entry = {"role": "assistant", "content": content or None}
            historical_calls = message.get("tool_calls") or []
            if historical_calls:
                mapped = []
                for position, call in enumerate(historical_calls):
                    call_id = f"call_{len(pending_calls) + position}"
                    pending_calls.append(call_id)
                    mapped.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": call.get("name", ""),
                                "arguments": _arguments_to_json(call.get("arguments")),
                            },
                        }
                    )
                entry["tool_calls"] = mapped
            converted.append(entry)
        elif role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not tool_call_id and pending_calls:
                tool_call_id = pending_calls.pop(0)
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id or f"call_{len(converted)}",
                    "content": content,
                }
            )
        else:
            converted.append({"role": role if role in ("system", "user") else "user", "content": content})
    return converted


def to_openai_tools(tools: list) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for tool in tools or []
    ]


def to_anthropic_messages(messages: list) -> tuple:
    """Split canonical messages into (system_text, anthropic_messages).

    Tool-role messages become user turns containing a readable ``[tool]``
    line: historical telemetry does not preserve the tool_use ids Anthropic
    requires, and replay is for comparison, not for continuing a live tool
    conversation.
    """
    system_parts = []
    converted = []
    for message in messages or []:
        role = message.get("role") or "user"
        content = _content_to_str(message.get("content"))
        if role == "system":
            if content:
                system_parts.append(content)
            continue
        if role == "tool":
            converted.append({"role": "user", "content": f"[tool result] {content}"})
        elif role == "assistant":
            converted.append({"role": "assistant", "content": content or "(assistant tool call)"})
        else:
            converted.append({"role": "user", "content": content})
    if converted and converted[0]["role"] != "user":
        converted.insert(0, {"role": "user", "content": "(context)"})
    return "\n".join(system_parts), converted


def to_anthropic_tools(tools: list) -> list:
    return [
        {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "input_schema": tool.get("parameters") or {"type": "object", "properties": {}},
        }
        for tool in tools or []
    ]


def to_bedrock_messages(messages: list) -> tuple:
    """Split canonical messages into (system_text, bedrock_converse_messages)."""
    system_text, converted = to_anthropic_messages(messages)
    return system_text, converted


def to_bedrock_tools(tools: list) -> dict:
    specs = [
        {
            "toolSpec": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "inputSchema": {"json": tool.get("parameters") or {"type": "object", "properties": {}}},
            }
        }
        for tool in tools or []
    ]
    return {"tools": specs} if specs else {}


# ---------------------------------------------------------------------------
# Live adapters
# ---------------------------------------------------------------------------


class OpenAICompatibleAdapter(ProviderAdapter):
    """OpenAI-compatible /chat/completions adapter, including local vLLM.

    Environment:
    - ``SWITCHIT_OPENAI_API_KEY`` or ``OPENAI_API_KEY`` — required.
    - ``SWITCHIT_OPENAI_BASE_URL`` — optional; default https://api.openai.com/v1.
    """

    name = "openai"
    kind = "live"
    remote = True

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS, remote: bool = False,
                 label: str | None = None, key_envs: tuple | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout
        self.remote = bool(remote)
        self.label = label or "openai"
        self.key_envs = key_envs or ("SWITCHIT_OPENAI_API_KEY", "OPENAI_API_KEY")

    def capabilities(self) -> dict:
        return {
            "tool_calling": True,
            "structured_output": True,
            "streaming": True,
            "caching": None,
            "max_context_tokens": None,
            "notes": (
                "Declared for OpenAI-compatible chat completions. Verify against the "
                "provider's current documentation; these are declarations, not measurements."
            ),
        }

    def _require_remote(self):
        if not self.remote:
            raise RemoteDisabledError(
                f"The '{self.label}' adapter is a live provider. It is disabled by default; "
                "re-run the replay command with --remote to enable data transfer to this provider."
            )
        if not self.api_key:
            env_hint = " or ".join(self.key_envs)
            raise ProviderError(
                f"Missing API key for '{self.label}'. Set {env_hint} in the environment; "
                "keys are never accepted on the command line."
            )

    def replay(self, request: dict, case: dict | None = None) -> dict:
        self._require_remote()
        messages = (request or {}).get("messages") or []
        tools = (request or {}).get("tools") or []
        payload = {"model": self.model, "messages": to_openai_messages(messages)}
        if tools:
            payload["tools"] = to_openai_tools(tools)
            payload["tool_choice"] = "auto"
        started = time.monotonic()
        try:
            body = post_json(
                f"{self.base_url}/chat/completions",
                payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except urllib.error.HTTPError as error:
            return self._error_result(error, started)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return self._error_result(error, started)
        latency_ms = int((time.monotonic() - started) * 1000)
        return self.parse_response(body, latency_ms)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _error_result(self, error, started) -> dict:
        detail = ""
        if isinstance(error, urllib.error.HTTPError):
            try:
                detail = error.read().decode("utf-8", "replace")[:300]
            except OSError:
                detail = ""
            message = f"HTTP {error.code} from {self.label}: {detail}"
        else:
            message = f"{type(error).__name__} contacting {self.label}: {error}"
        result = self._result(
            text="", tool_calls=[], finish_reason=None, error=message,
            latency_ms=int((time.monotonic() - started) * 1000), usage=_unknown_usage(),
            model=self.model,
        )
        result["adapter"] = self.name
        return result

    def parse_response(self, body: dict, latency_ms: int) -> dict:
        choices = body.get("choices") or []
        choice = choices[0] if choices else {}
        message = choice.get("message") or {}
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                arguments = {"_raw": str(function.get("arguments"))[:400]}
            tool_calls.append({"name": function.get("name", ""), "arguments": arguments})
        usage = (body.get("usage") or {})
        usage_dict = _recorded_usage(usage.get("prompt_tokens"), usage.get("completion_tokens"))
        result = self._result(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            error=None if choices else "response contained no choices",
            latency_ms=latency_ms,
            usage=usage_dict,
            model=body.get("model") or self.model,
        )
        result["adapter"] = self.name
        return result


class AnthropicAdapter(ProviderAdapter):
    """Anthropic Messages API adapter.

    Environment:
    - ``SWITCHIT_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY`` — required.
    - ``SWITCHIT_ANTHROPIC_BASE_URL`` — optional; default https://api.anthropic.com.
    """

    name = "anthropic"
    kind = "live"
    remote = True
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS, remote: bool = False,
                 max_tokens: int = ANTHROPIC_DEFAULT_MAX_TOKENS):
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.timeout = timeout
        self.remote = bool(remote)
        self.max_tokens = max_tokens

    def capabilities(self) -> dict:
        return {
            "tool_calling": True,
            "structured_output": True,
            "streaming": True,
            "caching": True,
            "max_context_tokens": None,
            "notes": (
                "Declared for the Messages API. Verify context limits and caching behavior "
                "against Anthropic's current documentation."
            ),
        }

    def _require_remote(self):
        if not self.remote:
            raise RemoteDisabledError(
                f"The '{self.name}' adapter is a live provider. It is disabled by default; "
                "re-run the replay command with --remote to enable data transfer to this provider."
            )
        if not self.api_key:
            raise ProviderError(
                f"Missing API key for '{self.name}'. Set SWITCHIT_ANTHROPIC_API_KEY or "
                "ANTHROPIC_API_KEY in the environment; keys are never accepted on the command line."
            )

    def replay(self, request: dict, case: dict | None = None) -> dict:
        self._require_remote()
        messages = (request or {}).get("messages") or []
        tools = (request or {}).get("tools") or []
        system_text, converted = to_anthropic_messages(messages)
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": converted,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = to_anthropic_tools(tools)
        started = time.monotonic()
        try:
            body = post_json(
                f"{self.base_url}/v1/messages",
                payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except urllib.error.HTTPError as error:
            return self._error_result(error, started)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return self._error_result(error, started)
        latency_ms = int((time.monotonic() - started) * 1000)
        return self.parse_response(body, latency_ms)

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _error_result(self, error, started) -> dict:
        detail = ""
        if isinstance(error, urllib.error.HTTPError):
            try:
                detail = error.read().decode("utf-8", "replace")[:300]
            except OSError:
                detail = ""
            message = f"HTTP {error.code} from {self.name}: {detail}"
        else:
            message = f"{type(error).__name__} contacting {self.name}: {error}"
        result = self._result(
            text="", tool_calls=[], finish_reason=None, error=message,
            latency_ms=int((time.monotonic() - started) * 1000), usage=_unknown_usage(),
            model=self.model,
        )
        result["adapter"] = self.name
        return result

    def parse_response(self, body: dict, latency_ms: int) -> dict:
        content = body.get("content") or []
        text_parts = []
        tool_calls = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {"name": block.get("name", ""), "arguments": dict(block.get("input") or {})}
                )
        usage = body.get("usage") or {}
        usage_dict = _recorded_usage(usage.get("input_tokens"), usage.get("output_tokens"))
        result = self._result(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=body.get("stop_reason"),
            error=None if content else "response contained no content blocks",
            latency_ms=latency_ms,
            usage=usage_dict,
            model=body.get("model") or self.model,
        )
        result["adapter"] = self.name
        return result


class BedrockConverseAdapter(ProviderAdapter):
    """Amazon Bedrock Converse adapter. Requires the optional boto3 package.

    Environment: standard AWS credential chain (``AWS_ACCESS_KEY_ID``,
    ``AWS_SECRET_ACCESS_KEY``, profiles, roles). Region from ``AWS_REGION`` or
    ``AWS_DEFAULT_REGION``. No keys are stored by this adapter.
    """

    name = "bedrock"
    kind = "live"
    remote = True

    def __init__(self, model: str, region: str | None = None, profile: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS, remote: bool = False):
        self.model = model
        self.region = region
        self.profile = profile
        self.timeout = timeout
        self.remote = bool(remote)
        self._client = None

    def capabilities(self) -> dict:
        return {
            "tool_calling": True,
            "structured_output": True,
            "streaming": True,
            "caching": None,
            "max_context_tokens": None,
            "notes": (
                "Declared for the Converse API and model-specific limits. Verify against "
                "the current AWS documentation for the specific model id."
            ),
        }

    def _require_remote(self):
        if not self.remote:
            raise RemoteDisabledError(
                f"The '{self.name}' adapter is a live provider. It is disabled by default; "
                "re-run the replay command with --remote to enable data transfer to this provider."
            )

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # imported lazily; optional dependency
            from botocore.config import Config
        except ImportError as error:
            raise ProviderError(
                "The Bedrock adapter requires boto3. Install it with: pip install boto3"
            ) from error
        session_kwargs = {}
        if self.profile:
            session_kwargs["profile_name"] = self.profile
        region = self.region or region_from_env()
        client_kwargs = {
            "service_name": "bedrock-runtime",
            "config": Config(read_timeout=self.timeout, connect_timeout=min(30, self.timeout)),
        }
        if region:
            client_kwargs["region_name"] = region
        if session_kwargs:
            from boto3.session import Session

            self._client = Session(**session_kwargs).client(**client_kwargs)
        else:
            self._client = boto3.client(**client_kwargs)
        return self._client

    def replay(self, request: dict, case: dict | None = None) -> dict:
        self._require_remote()
        client = self._get_client()
        messages = (request or {}).get("messages") or []
        tools = (request or {}).get("tools") or []
        system_text, converted = to_bedrock_messages(messages)
        kwargs = {"modelId": self.model, "messages": converted}
        if system_text:
            kwargs["system"] = [{"text": system_text}]
        tool_config = to_bedrock_tools(tools)
        if tool_config:
            kwargs["toolConfig"] = tool_config
        started = time.monotonic()
        try:
            body = client.converse(**kwargs)
        except Exception as error:  # botocore exceptions vary by version
            result = self._result(
                text="", tool_calls=[], finish_reason=None,
                error=f"{type(error).__name__} from bedrock: {error}",
                latency_ms=int((time.monotonic() - started) * 1000),
                usage=_unknown_usage(), model=self.model,
            )
            result["adapter"] = self.name
            return result
        latency_ms = int((time.monotonic() - started) * 1000)
        return self.parse_response(body, latency_ms)

    @staticmethod
    def parse_response(body: dict, latency_ms: int, model: str = "bedrock") -> dict:
        output = body.get("output") or {}
        message = output.get("message") or {}
        text_parts = []
        tool_calls = []
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif block.get("toolUse"):
                tool_use = block["toolUse"]
                tool_calls.append(
                    {"name": tool_use.get("name", ""), "arguments": dict(tool_use.get("input") or {})}
                )
        usage = body.get("usage") or {}
        usage_dict = _recorded_usage(usage.get("inputTokens"), usage.get("outputTokens"))
        result = ProviderAdapter._result(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=body.get("stopReason"),
            error=None if message else "response contained no output message",
            latency_ms=latency_ms,
            usage=usage_dict,
            model=model,
        )
        result["adapter"] = "bedrock"
        return result


def region_from_env() -> str:
    """Read the AWS region from the standard environment variables."""
    import os

    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""


def post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    """POST JSON and parse the JSON response. Errors raise urllib exceptions."""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError as error:
        raise ProviderError(f"{url} returned non-JSON response: {raw[:200]}") from error


ADAPTER_KINDS = {
    "recorded": RecordedBaselineAdapter,
    "mock": MockAdapter,
    "openai": OpenAICompatibleAdapter,
    "anthropic": AnthropicAdapter,
    "bedrock": BedrockConverseAdapter,
}

LIVE_ADAPTERS = ("openai", "anthropic", "bedrock")

# ---------------------------------------------------------------------------
# Provider presets — OpenAI-compatible endpoints with the details filled in,
# so `--target grok` works without the user having to know base URLs.
#
# Meta Llama is a model family, not an endpoint: it is reachable through
# `ollama` or `vllm` (local, free), `together` / `fireworks` (hosted), or
# `bedrock` (Llama on AWS). Memory services such as Supermemory are business
# tools the agent calls, not chat-completion providers, so they are captured
# as tool proposals during replay — they are not replay targets.
# ---------------------------------------------------------------------------

PROVIDER_PRESETS = {
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "default_model": "grok-4",
        "key_envs": ("XAI_API_KEY", "GROK_API_KEY"),
        "note": "xAI Grok via its OpenAI-compatible endpoint.",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "key_envs": ("GROQ_API_KEY",),
        "note": "Groq's OpenAI-compatible endpoint.",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "key_envs": ("DEEPSEEK_API_KEY",),
        "note": "DeepSeek's OpenAI-compatible endpoint.",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "key_envs": ("MISTRAL_API_KEY",),
        "note": "Mistral's OpenAI-compatible endpoint.",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "key_envs": ("OPENROUTER_API_KEY",),
        "note": "OpenRouter routes to hundreds of models; model ids are provider-prefixed.",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "key_envs": ("TOGETHER_API_KEY",),
        "note": "Together AI; common home for hosted Llama models.",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "key_envs": ("FIREWORKS_API_KEY",),
        "note": "Fireworks AI; also hosts Llama models.",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.2",
        "key_envs": (),
        "note": "Ollama on localhost — a free, fully local live target. No key needed.",
    },
    "vllm": {
        "base_url": "http://localhost:8000/v1",
        "default_model": None,
        "key_envs": (),
        "note": "Local vLLM server; pass --target-model (it serves whatever you loaded).",
    },
}


def list_presets() -> dict:
    """The preset registry, for display in the CLI, MCP, and docs."""
    return {
        name: {"base_url": spec["base_url"], "default_model": spec["default_model"],
               "key_envs": list(spec["key_envs"]), "note": spec["note"]}
        for name, spec in PROVIDER_PRESETS.items()
    }


def is_preset(kind: str) -> bool:
    return kind in PROVIDER_PRESETS


def resolve_preset(name: str, overrides: dict | None = None) -> dict:
    """Resolve a preset name into an OpenAI-adapter config.

    Returns {"kind": "openai", "base_url", "model", "key_envs", "preset"} with
    caller-supplied overrides winning over preset defaults. Raises for unknown
    presets. No secret is read here; the caller resolves keys from the env.
    """
    if name not in PROVIDER_PRESETS:
        raise ProviderError(
            f"Unknown provider preset {name!r}; known presets: {', '.join(sorted(PROVIDER_PRESETS))}"
        )
    spec = PROVIDER_PRESETS[name]
    overrides = dict(overrides or {})
    config = {
        "kind": "openai",
        "preset": name,
        "base_url": overrides.get("base_url") or spec["base_url"],
        "model": overrides.get("model") or spec["default_model"],
        "key_envs": list(spec["key_envs"]),
        "note": spec["note"],
    }
    if config["model"] is None:
        raise ProviderError(
            f"The '{name}' preset serves whatever model you loaded; pass one with "
            "--target-model (CLI) or target_model (MCP)."
        )
    return config


def read_preset_key(name: str):
    """Read the first present env var for a preset's API key. Never logs it."""
    import os

    if name not in PROVIDER_PRESETS:
        return None
    for env_name in PROVIDER_PRESETS[name]["key_envs"]:
        value = os.environ.get(env_name)
        if value:
            return value
    return None


def build_adapter(kind: str, config: dict | None = None, remote: bool = False) -> ProviderAdapter:
    """Build an adapter from a kind or preset name and a config dict (no secrets in it)."""
    config = dict(config or {})
    if kind in PROVIDER_PRESETS:
        resolved = resolve_preset(kind, config)
        kind = "openai"
        config = {**resolved, **{k: v for k, v in config.items() if v is not None}}
        config["kind"] = "openai"
    if kind not in ADAPTER_KINDS:
        raise ProviderError(
            f"Unknown adapter or preset {kind!r}; known kinds: {', '.join(sorted(ADAPTER_KINDS))}; "
            f"known presets: {', '.join(sorted(PROVIDER_PRESETS))}"
        )
    if kind == "recorded":
        return RecordedBaselineAdapter(model=config.get("model"))
    if kind == "mock":
        return MockAdapter(
            mode=config.get("mock_mode", "mirror"),
            text=config.get("mock_text"),
            tool_calls=config.get("mock_tool_calls"),
            model=config.get("model", "mock-model"),
        )
    if kind == "openai":
        return OpenAICompatibleAdapter(
            model=config.get("model") or "gpt-4o-mini",
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            timeout=config.get("timeout", DEFAULT_TIMEOUT_SECONDS),
            remote=remote,
            label=config.get("preset") or config.get("label") or "openai",
            key_envs=tuple(config.get("key_envs") or ("SWITCHIT_OPENAI_API_KEY", "OPENAI_API_KEY")),
        )
    if kind == "anthropic":
        return AnthropicAdapter(
            model=config.get("model") or "claude-sonnet-4-5",
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            timeout=config.get("timeout", DEFAULT_TIMEOUT_SECONDS),
            remote=remote,
            max_tokens=config.get("max_tokens", ANTHROPIC_DEFAULT_MAX_TOKENS),
        )
    return BedrockConverseAdapter(
        model=config.get("model") or "amazon.nova-micro-v1:0",
        region=config.get("region"),
        profile=config.get("profile"),
        timeout=config.get("timeout", DEFAULT_TIMEOUT_SECONDS),
        remote=remote,
    )


def adapter_spec(kind: str, config: dict | None = None) -> dict:
    """Fingerprint-friendly description of an adapter; contains no secrets."""
    config = dict(config or {})
    if kind in PROVIDER_PRESETS:
        resolved = resolve_preset(kind, config)
        spec = {"kind": "openai", "preset": kind}
        for key in ("model", "base_url", "timeout"):
            value = config.get(key) if config.get(key) is not None else resolved.get(key)
            if value is not None:
                spec[key] = value
        return spec
    safe_keys = ("model", "mock_mode", "mock_text", "base_url", "region", "profile", "timeout")
    return {"kind": kind, **{key: config.get(key) for key in safe_keys if config.get(key) is not None}}
