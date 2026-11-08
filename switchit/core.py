"""Import, normalize, redact, workflow mapping, and replay-case selection.

This module owns the canonical trace schema and every transformation that
produces it:

- Importers for JSON/JSONL, OpenTelemetry/OTLP JSON, Langfuse-style exports,
  and Parquet (optional dependency).
- Redaction of secrets before anything is stored, so redacted content is the
  only content that ever reaches reports or live replay.
- Normalization of heterogeneous call records into linked sessions, traces,
  and calls with usage provenance (``recorded`` / ``estimated`` / ``unknown``).
- Provisional workflow grouping with evidence and uncertainty signals.
- Representative replay-case selection with editable default success checks.

The grouping is deliberately simple (TF-IDF cosine + tool-set overlap with
greedy assignment). It is a triage aid for a human or agent reviewer, not a
measured-accuracy classifier.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path

STORE_VERSION = 1
WORKFLOW_MAP_VERSION = 1
CASES_VERSION = 1

# Content stored for any single message is capped with head/tail elision so a
# pathological historical payload cannot flood the store or the report.
MESSAGE_CAP = 8000
EVIDENCE_CAP = 1200
MAX_USER_REQUESTS_PER_TRACE = 4

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_LABEL_PATTERN = re.compile(
    r"(?i)\b(password|passwd|api[_ -]?key|apikey|secret|token|bearer|authorization)"
    r"(\s*[:=]\s*|\s+)[^\s,;\"']+"
)
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_AWS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_SK_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_WHITESPACE_PATTERN = re.compile(r"\s+")


def redact_text(value) -> str:
    """Redact obvious secrets from free text.

    Emails, phone-like numbers, AWS access keys, ``sk-``-style API keys and
    ``password=``/``bearer``-style assignments are replaced with markers.
    Whitespace is collapsed so excerpts stay compact in reports.
    """
    if value is None:
        return ""
    text = _WHITESPACE_PATTERN.sub(" ", str(value)).strip()
    text = _SECRET_LABEL_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = _AWS_KEY_PATTERN.sub("[REDACTED_KEY]", text)
    text = _SK_KEY_PATTERN.sub("[REDACTED_KEY]", text)
    text = _EMAIL_PATTERN.sub("[EMAIL]", text)
    text = _PHONE_PATTERN.sub("[NUMBER]", text)
    return text


def excerpt(text: str, limit: int = MESSAGE_CAP) -> str:
    """Cap text length with head/tail elision so both ends stay visible."""
    text = redact_text(text)
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.75))
    tail = max(1, limit - head - 5)
    return text[:head] + " […] " + text[-tail:]


def redact_value(value):
    """Deep-walk any JSON-ish value, redacting every string inside it."""
    if isinstance(value, str):
        return excerpt(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def redact_messages(messages) -> list:
    """Normalize message dicts to {role, content} plus optional fields, redacted."""
    clean = []
    for message in messages or []:
        if not isinstance(message, dict):
            clean.append({"role": "user", "content": excerpt(str(message))})
            continue
        entry = {
            "role": str(message.get("role") or "user"),
            "content": excerpt(message.get("content") if message.get("content") is not None else ""),
        }
        for key in ("name", "tool_call_id"):
            if message.get(key):
                entry[key] = redact_text(str(message[key]))[:200]
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            entry["tool_calls"] = redact_value(calls)
        clean.append(entry)
    return clean


def redact_tools(tools) -> list:
    """Normalize tool schemas; descriptions are redacted, names are kept short."""
    clean = []
    for tool in tools or []:
        if isinstance(tool, dict):
            if "function" in tool and isinstance(tool["function"], dict):
                tool = tool["function"]
            entry = {
                "name": redact_text(str(tool.get("name") or ""))[:200],
                "description": excerpt(str(tool.get("description") or ""), 400),
                "parameters": redact_value(tool.get("parameters") or {}),
            }
            if entry["name"]:
                clean.append(entry)
        elif isinstance(tool, str):
            name = redact_text(tool)[:200]
            if name:
                clean.append({"name": name, "description": "", "parameters": {}})
    return clean


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------


def empty_usage() -> dict:
    """Per-basis usage counters. Every call is counted exactly once."""
    return {
        basis: {
            "calls": 0,
            "input_tokens_known": 0,
            "output_tokens_known": 0,
            "input_missing_calls": 0,
            "output_missing_calls": 0,
            "calls_missing_either": 0,
        }
        for basis in ("recorded", "estimated", "unknown")
    }


def normalize_basis(basis, estimator=None):
    basis = (basis or "unknown").strip().lower()
    if basis.startswith("source_reported") or basis in ("recorded", "source"):
        return "recorded", estimator
    if basis.startswith("estimated"):
        return "estimated", estimator or basis
    return "unknown", estimator


def add_usage(usage: dict, input_tokens, output_tokens, basis: str, estimator=None) -> dict:
    """Add one call's usage to a per-basis aggregate. Missing values stay visible."""
    basis, estimator = normalize_basis(basis, estimator)
    bucket = usage.setdefault(
        basis,
        {
            "calls": 0,
            "input_tokens_known": 0,
            "output_tokens_known": 0,
            "input_missing_calls": 0,
            "output_missing_calls": 0,
            "calls_missing_either": 0,
        },
    )
    bucket["calls"] += 1
    for key, value in (("input", input_tokens), ("output", output_tokens)):
        if value is None:
            bucket[f"{key}_missing_calls"] += 1
        else:
            try:
                bucket[f"{key}_tokens_known"] += int(value)
            except (TypeError, ValueError):
                bucket[f"{key}_missing_calls"] += 1
    if input_tokens is None or output_tokens is None:
        bucket["calls_missing_either"] += 1
    return usage


def merge_usage(*usages: dict) -> dict:
    merged = empty_usage()
    for usage in usages:
        if not usage:
            continue
        for basis, bucket in usage.items():
            target = merged.setdefault(basis, empty_usage()[basis])
            for key, value in bucket.items():
                target[key] = target.get(key, 0) + value
    return merged


def usage_from_record(raw: dict):
    """Extract (input_tokens, output_tokens, basis, estimator) from a raw record."""
    usage = raw.get("usage")
    input_tokens = raw.get("input_tokens")
    output_tokens = raw.get("output_tokens")
    basis = raw.get("usage_basis")
    estimator = raw.get("usage_estimator")
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", input_tokens)
        output_tokens = usage.get("output_tokens", output_tokens)
        basis = usage.get("basis", basis)
        estimator = usage.get("estimator", estimator)
    elif usage is not None:
        basis = basis or "recorded"
    for key in ("estimated_input_tokens", "est_input_tokens"):
        if input_tokens is None and raw.get(key) is not None:
            input_tokens = raw[key]
            basis = basis or "estimated"
    for key in ("estimated_output_tokens", "est_output_tokens"):
        if output_tokens is None and raw.get(key) is not None:
            output_tokens = raw[key]
            basis = basis or "estimated"
    if basis is None and (input_tokens is not None or output_tokens is not None):
        basis = "recorded"
    if input_tokens is None and output_tokens is None and not basis:
        basis = "unknown"
    return input_tokens, output_tokens, basis, estimator


# ---------------------------------------------------------------------------
# Canonical schema helpers
# ---------------------------------------------------------------------------


def _stable_id(*parts) -> str:
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8"))
    return digest.hexdigest()[:24]


def normalize_tool_call(raw) -> dict:
    """Coerce a historical tool call into {name, arguments-dict}."""
    if not isinstance(raw, dict):
        return {"name": redact_text(str(raw))[:200], "arguments": {}}
    function = raw.get("function") if isinstance(raw.get("function"), dict) else None
    name = raw.get("name") or (function or {}).get("name") or ""
    arguments = raw.get("arguments", raw.get("args", (function or {}).get("arguments", {})))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {"_raw": arguments[:400]}
    if not isinstance(arguments, dict):
        arguments = {}
    return {"name": redact_text(str(name))[:200], "arguments": redact_value(arguments)}


def normalize_response(response) -> dict:
    """Coerce any historical output shape into the canonical response dict."""
    result = {"text": "", "tool_calls": [], "finish_reason": None, "error": None}
    if response is None:
        return result
    if isinstance(response, str):
        result["text"] = excerpt(response)
        return result
    if not isinstance(response, dict):
        result["text"] = excerpt(str(response))
        return result

    def absorb_message(message: dict) -> None:
        role = str(message.get("role") or "")
        if role and role != "assistant" and not message.get("tool_calls"):
            return
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    result["text"] = excerpt((result["text"] + " " + block["text"]).strip())
                elif isinstance(block, str):
                    result["text"] = excerpt((result["text"] + " " + block).strip())
        elif isinstance(content, str) and content:
            result["text"] = excerpt((result["text"] + " " + content).strip())
        for call in message.get("tool_calls") or []:
            if call:
                result["tool_calls"].append(normalize_tool_call(call))

    messages = response.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                absorb_message(message)
    message = response.get("message")
    if isinstance(message, dict):
        absorb_message(message)
        if result["finish_reason"] is None:
            result["finish_reason"] = message.get("finish_reason", message.get("stop_reason"))
        if result["error"] is None and message.get("error"):
            result["error"] = redact_text(str(message["error"]))[:400]

    if not result["tool_calls"]:
        for call in response.get("tool_calls") or []:
            if call:
                result["tool_calls"].append(normalize_tool_call(call))
    if not result["text"]:
        content = response.get("content", response.get("text", response.get("output", "")))
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if isinstance(block.get("text"), str):
                        parts.append(block["text"])
                    if block.get("type") in ("tool_use", "function_call") or block.get("name"):
                        result["tool_calls"].append(normalize_tool_call(block))
                elif isinstance(block, str):
                    parts.append(block)
            result["text"] = excerpt(" ".join(p for p in parts if p))
        else:
            result["text"] = excerpt(content if content is not None else "")
    if result["finish_reason"] is None:
        result["finish_reason"] = response.get("finish_reason", response.get("stop_reason"))
    if result["error"] is None and response.get("error") is not None:
        result["error"] = redact_text(str(response["error"]))[:400]
    return result


def normalize_request(request) -> dict:
    """Coerce any historical input shape into {"messages": [...], "tools": [...]}."""
    messages = []
    tools = []
    if isinstance(request, dict):
        raw_messages = request.get("messages", request.get("input_messages"))
        if isinstance(raw_messages, str):
            try:
                raw_messages = json.loads(raw_messages)
            except ValueError:
                raw_messages = [{"role": "user", "content": raw_messages}]
        if isinstance(raw_messages, list):
            messages = raw_messages
        raw_tools = request.get("tools", request.get("tool_definitions"))
        if isinstance(raw_tools, list):
            tools = raw_tools
        if not messages and "prompt" in request:
            messages = [{"role": "user", "content": str(request.get("prompt"))}]
    elif isinstance(request, str):
        try:
            parsed = json.loads(request)
            return normalize_request(parsed)
        except ValueError:
            messages = [{"role": "user", "content": request}]
    elif isinstance(request, list):
        messages = request
    return {"messages": redact_messages(messages), "tools": redact_tools(tools)}


def make_call(raw: dict, importer: str, source_location: str) -> dict:
    """Build one canonical call from a raw record. Original IDs are preserved."""
    trace_id = raw.get("trace_id") or raw.get("traceId") or raw.get("conversation_id")
    if not trace_id:
        raise ValueError(f"{source_location}: record has no trace_id")
    call_id = raw.get("call_id") or raw.get("id") or raw.get("span_id") or raw.get("observation_id")
    call_index = raw.get("call_index")
    if call_index is None:
        call_index = raw.get("index")
    span_id = raw.get("span_id") or raw.get("spanId")
    session_id = raw.get("session_id") or raw.get("sessionId") or raw.get("session")
    input_tokens, output_tokens, basis, estimator = usage_from_record(raw)
    response = normalize_response(raw.get("response"))
    error = raw.get("error")
    if error is not None:
        if isinstance(error, str):
            response["error"] = redact_text(error)[:400] or response["error"]
        else:
            response["error"] = json.dumps(redact_value(error))[:400]
    return {
        "call_id": str(call_id) if call_id else _stable_id("call", importer, source_location, trace_id, call_index),
        "trace_id": str(trace_id),
        "session_id": str(session_id) if session_id else None,
        "span_id": str(span_id) if span_id else None,
        "call_index": int(call_index) if call_index is not None else 0,
        "provider": redact_text(str(raw.get("provider") or raw.get("gen_ai_system") or "unknown"))[:120],
        "model": redact_text(str(raw.get("model") or "unknown"))[:160],
        "request": normalize_request(raw.get("request")),
        "response": response,
        "latency_ms": _coerce_latency(raw.get("latency_ms")),
        "usage": {
            "input_tokens": _coerce_int(input_tokens),
            "output_tokens": _coerce_int(output_tokens),
            "basis": normalize_basis(basis, estimator)[0],
            "estimator": estimator,
        },
        "source": {"importer": importer, "location": str(source_location)},
    }


def _coerce_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_latency(value):
    if value is None:
        return None
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return None


def build_traces(calls: list, session_meta: dict | None = None) -> list:
    """Aggregate canonical calls into traces with usage and evidence summaries."""
    grouped = defaultdict(list)
    for call in calls:
        grouped[call["trace_id"]].append(call)
    traces = []
    for trace_id in sorted(grouped):
        members = sorted(grouped[trace_id], key=lambda c: (c["call_index"], c["call_id"]))
        usage = empty_usage()
        error_count = 0
        tool_names = []
        user_requests = []
        latency_values = []
        session_id = None
        meta = (session_meta or {}).get(trace_id) or {}
        for call in members:
            add_usage(
                usage,
                call["usage"]["input_tokens"],
                call["usage"]["output_tokens"],
                call["usage"]["basis"],
                call["usage"]["estimator"],
            )
            response = call["response"]
            if response["error"]:
                error_count += 1
            for tool_call in response["tool_calls"]:
                if tool_call["name"] and tool_call["name"] not in tool_names:
                    tool_names.append(tool_call["name"])
            for tool_call in call["request"]["tools"]:
                if tool_call["name"] and tool_call["name"] not in tool_names:
                    tool_names.append(tool_call["name"])
            for message in call["request"]["messages"]:
                if message.get("role") == "user" and message.get("content"):
                    user_requests.append(message["content"])
            if call["latency_ms"] is not None:
                latency_values.append(call["latency_ms"])
            if call["session_id"]:
                session_id = call["session_id"]
        deduped_requests = []
        seen = set()
        for request in user_requests:
            key = request.casefold()[:200]
            if key in seen:
                continue
            seen.add(key)
            deduped_requests.append(request)
        traces.append(
            {
                "trace_id": trace_id,
                "session_id": session_id,
                "call_ids": [call["call_id"] for call in members],
                "call_count": len(members),
                "error_count": error_count,
                "tool_names": tool_names,
                "user_requests": deduped_requests[:MAX_USER_REQUESTS_PER_TRACE],
                "latency_ms_max": max(latency_values) if latency_values else None,
                "usage": usage,
            }
        )
        if meta:
            for key in ("title", "origin", "cwd"):
                if meta.get(key):
                    traces[-1][key] = meta[key]
    return traces


def build_sessions(traces: list) -> list:
    sessions = defaultdict(lambda: {"session_id": None, "trace_ids": [], "call_count": 0})
    for trace in traces:
        if not trace.get("session_id"):
            continue
        session = sessions[trace["session_id"]]
        session["session_id"] = trace["session_id"]
        session["trace_ids"].append(trace["trace_id"])
        session["call_count"] += trace["call_count"]
    return [sessions[key] for key in sorted(sessions)]


def build_store(calls: list, app_config=None, source_files=None, notes=None,
                session_meta: dict | None = None) -> dict:
    """Assemble the canonical store from canonical calls.

    ``session_meta`` optionally maps trace_id to {"title", "origin", "cwd"}
    (e.g. Codex's cloud/local thread catalog) and is merged into the traces.
    """
    traces = build_traces(calls, session_meta=session_meta)
    return {
        "store_version": STORE_VERSION,
        "app_config": redact_value(app_config) if app_config else None,
        "sessions": build_sessions(traces),
        "traces": traces,
        "calls": calls,
        "totals": {
            "traces": len(traces),
            "calls": len(calls),
            "usage": merge_usage(*[trace["usage"] for trace in traces]),
        },
        "source_files": list(source_files or []),
        "notes": list(notes or []),
    }


# ---------------------------------------------------------------------------
# Atomic persistence
# ---------------------------------------------------------------------------


def atomic_write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_path = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1, sort_keys=False)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def save_store(store: dict, path) -> None:
    atomic_write_json(path, store)


def load_store(path) -> dict:
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def digest_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_data(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Importers — each yields raw records for make_call()
# ---------------------------------------------------------------------------


def _parse_json_value(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def parse_jsonl_lines(lines, source: str) -> list:
    """Parse generic JSON/JSONL records. Accepts flexible key spellings."""
    raw_calls = []
    for line_number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError as error:
            raise ValueError(f"{source}:{line_number}: invalid JSON ({error})") from error
        if isinstance(record, list):
            for item in record:
                raw_calls.append(_jsonl_record(item, f"{source}:{line_number}"))
        else:
            raw_calls.append(_jsonl_record(record, f"{source}:{line_number}"))
    return raw_calls


def _jsonl_record(record: dict, location: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError(f"{location}: record is not an object")
    request = record.get("request", record.get("input"))
    if request is None:
        messages = _parse_json_value(record.get("messages", record.get("input_messages")), None)
        if messages is None:
            messages = _parse_json_value(record.get("prompt"), None)
            request = {"prompt": messages} if isinstance(messages, str) else (messages or [])
        else:
            request = {"messages": messages, "tools": _parse_json_value(record.get("tools"), [])}
    response = record.get("response", record.get("output"))
    if response is None:
        output_messages = _parse_json_value(record.get("output_messages"), None)
        if output_messages is not None:
            response = {"messages": output_messages}
        else:
            text = record.get("response_text", record.get("output_text"))
            response = (
                {"text": text, "tool_calls": _parse_json_value(record.get("tool_calls"), []),
                 "finish_reason": record.get("finish_reason"), "error": record.get("error")}
                if text is not None or record.get("tool_calls") else None
            )
    return {
        "trace_id": record.get("trace_id", record.get("traceId", record.get("conversation_id"))),
        "call_id": record.get("call_id", record.get("id")),
        "session_id": record.get("session_id", record.get("sessionId", record.get("session"))),
        "span_id": record.get("span_id", record.get("spanId")),
        "call_index": record.get("call_index", record.get("index")),
        "provider": record.get("provider"),
        "model": record.get("model"),
        "request": request,
        "response": response,
        "latency_ms": record.get("latency_ms"),
        "error": record.get("error"),
        "input_tokens": record.get("input_tokens"),
        "output_tokens": record.get("output_tokens"),
        "usage": record.get("usage"),
        "usage_basis": record.get("usage_basis"),
        "usage_estimator": record.get("usage_estimator", record.get("estimator")),
    }


def import_jsonl(path) -> list:
    """Import JSON or JSONL call records from a file path."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as stream:
        return parse_jsonl_lines(stream, str(path))


# --- OTLP ------------------------------------------------------------------


def _otlp_attr_value(value: dict):
    if not isinstance(value, dict):
        return None
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value and isinstance(value["arrayValue"], dict):
        return [item.get("stringValue") for item in value["arrayValue"].get("values", [])]
    return None


def _otlp_attributes(span: dict) -> dict:
    attributes = {}
    for attribute in span.get("attributes", []) or []:
        key = attribute.get("key")
        if key:
            attributes[key] = _otlp_attr_value(attribute.get("value"))
    return attributes


def _otlp_message_list(attributes: dict, prefix: str) -> list:
    """Read gen_ai.prompt.{n}.role/.content style attributes into messages."""
    pattern = re.compile(rf"^{re.escape(prefix)}\.(\d+)\.(role|content)$")
    messages = {}
    for key, value in attributes.items():
        match = pattern.match(key)
        if not match:
            continue
        entry = messages.setdefault(int(match.group(1)), {})
        if match.group(2) == "role":
            entry["role"] = value
        elif isinstance(value, list):
            entry["content"] = " ".join(str(item) for item in value if item is not None)
        else:
            entry["content"] = value
    return [messages[index] for index in sorted(messages)]


def import_otlp(path) -> list:
    """Import OpenTelemetry/OTLP JSON spans using gen_ai semantic conventions."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as stream:
        document = json.load(stream)
    raw_calls = []
    resource_spans = document.get("resourceSpans") or []
    for resource_index, resource in enumerate(resource_spans):
        resource_attributes = _otlp_attributes(resource.get("resource") or {})
        for scope in resource.get("scopeSpans", []) or []:
            for span_index, span in enumerate(scope.get("spans", []) or []):
                attributes = _otlp_attributes(span)
                merged = {**resource_attributes, **attributes}
                gen_ai_keys = [key for key in merged if key.startswith("gen_ai.")]
                if not gen_ai_keys:
                    continue
                messages = []
                for prefix in ("gen_ai.input", "gen_ai.prompt", "gen_ai.request"):
                    candidate = _otlp_message_list(merged, prefix)
                    if candidate:
                        messages = candidate
                        break
                output_messages = _otlp_message_list(merged, "gen_ai.output")
                if not output_messages:
                    output_messages = _otlp_message_list(merged, "gen_ai.completion")
                error = None
                status = span.get("status") or {}
                if str(status.get("code", "")) not in ("", "1", "STATUS_CODE_UNSET", "UNSET"):
                    error = status.get("message") or "span status error"
                tool_definitions = []
                for key, value in merged.items():
                    if key.startswith("gen_ai.tool.definition") and isinstance(value, str):
                        try:
                            tool_definitions.append(json.loads(value))
                        except ValueError:
                            tool_definitions.append({"name": value})
                raw_calls.append(
                    {
                        "trace_id": span.get("traceId"),
                        "span_id": span.get("spanId"),
                        "call_index": span_index,
                        "provider": merged.get("gen_ai.system"),
                        "model": merged.get("gen_ai.request.model", merged.get("gen_ai.response.model")),
                        "session_id": merged.get("session.id", merged.get("gen_ai.session.id")),
                        "request": {"messages": messages, "tools": tool_definitions},
                        "response": {
                            "messages": output_messages,
                            "finish_reason": merged.get("gen_ai.response.finish_reasons"),
                            "error": error,
                        },
                        "latency_ms": _otlp_latency_ms(span),
                        "input_tokens": merged.get("gen_ai.usage.input_tokens",
                                                   merged.get("gen_ai.usage.prompt_tokens")),
                        "output_tokens": merged.get("gen_ai.usage.output_tokens",
                                                    merged.get("gen_ai.usage.completion_tokens")),
                        "usage_basis": "recorded" if (
                            merged.get("gen_ai.usage.input_tokens") is not None
                            or merged.get("gen_ai.usage.output_tokens") is not None
                        ) else "unknown",
                    }
                )
    return raw_calls


def _otlp_latency_ms(span: dict):
    try:
        start = int(span.get("startTimeUnixNano"))
        end = int(span.get("endTimeUnixNano"))
    except (TypeError, ValueError):
        return None
    return max(0, (end - start) // 1_000_000)


# --- Langfuse ---------------------------------------------------------------


def import_langfuse(path) -> list:
    """Import a Langfuse-style trace export (traces with observations)."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as stream:
        document = json.load(stream)
    raw_calls = []
    traces = document.get("data") if isinstance(document, dict) else document
    for trace in traces or []:
        trace_id = trace.get("id")
        session_id = trace.get("sessionId") or trace.get("session_id")
        observations = trace.get("observations") or []
        generations = [o for o in observations if str(o.get("type", "")).upper() == "GENERATION"]
        if not generations and observations:
            generations = observations
        for index, observation in enumerate(generations):
            usage = observation.get("usage") or {}
            input_tokens = usage.get("input")
            output_tokens = usage.get("output")
            basis = "recorded" if (input_tokens is not None or output_tokens is not None) else "unknown"
            error = None
            if str(observation.get("level", "")).upper() == "ERROR":
                error = observation.get("statusMessage") or "observation level ERROR"
            latency_ms = None
            try:
                from datetime import datetime

                start = datetime.fromisoformat(str(observation.get("startTime")).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(observation.get("endTime")).replace("Z", "+00:00"))
                latency_ms = max(0, int((end - start).total_seconds() * 1000))
            except (ValueError, TypeError):
                latency_ms = None
            model_parameters = observation.get("modelParameters")
            if not isinstance(model_parameters, dict):
                model_parameters = {}
            raw_calls.append(
                {
                    "trace_id": trace_id,
                    "call_id": observation.get("id"),
                    "session_id": session_id,
                    "call_index": index,
                    "provider": observation.get("provider") or model_parameters.get("provider"),
                    "model": observation.get("model"),
                    "request": observation.get("input"),
                    "response": observation.get("output"),
                    "latency_ms": latency_ms,
                    "error": error,
                    "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "basis": basis},
                }
            )
    return raw_calls


# --- Parquet (optional dependency) ------------------------------------------


PARQUET_BASIS_MAP = {
    "source_reported": "recorded",
    "reported": "recorded",
    "recorded": "recorded",
}


def import_parquet(path) -> list:
    """Import the Parquet trace shape; requires the optional pyarrow package."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:  # pragma: no cover - depends on environment
        raise ImportError(
            "Parquet import requires pyarrow. Install it with: pip install pyarrow"
        ) from error
    path = Path(path)
    if path.suffix.lower() == ".zip":
        import zipfile

        with zipfile.ZipFile(path) as archive:
            matches = [name for name in archive.namelist() if Path(name).name == "traces.parquet"]
            if len(matches) != 1:
                raise ValueError("ZIP must contain exactly one traces.parquet")
            data = archive.read(matches[0])
        parquet_file = pq.ParquetFile(pa.BufferReader(data))
    else:
        parquet_file = pq.ParquetFile(path)
    columns = [
        "trace_id", "span_id", "call_index", "input_tokens", "output_tokens",
        "usage_basis", "input_messages_json", "output_messages_json", "extra_json",
    ]
    raw_calls = []
    for batch in parquet_file.iter_batches(batch_size=200, columns=columns):
        for row in batch.to_pylist():
            extra = _parse_json_value(row.get("extra_json"), {})
            if not isinstance(extra, dict):
                extra = {}
            messages = _parse_json_value(row.get("input_messages_json"), [])
            if isinstance(extra.get("system_instructions"), str) and extra["system_instructions"].strip():
                messages = [{"role": "system", "content": extra["system_instructions"]}] + list(messages or [])
            error = extra.get("error")
            raw_calls.append(
                {
                    "trace_id": row.get("trace_id"),
                    "span_id": row.get("span_id"),
                    "call_index": row.get("call_index"),
                    "model": extra.get("model"),
                    "provider": extra.get("provider"),
                    "session_id": extra.get("session_id"),
                    "request": {"messages": messages},
                    "response": {"messages": _parse_json_value(row.get("output_messages_json"), []),
                                 "error": error,
                                 "finish_reason": extra.get("finish_reason")},
                    "latency_ms": extra.get("latency_ms"),
                    "input_tokens": row.get("input_tokens"),
                    "output_tokens": row.get("output_tokens"),
                    "usage_basis": PARQUET_BASIS_MAP.get(str(row.get("usage_basis") or "").lower(),
                                                         row.get("usage_basis") or "unknown"),
                    "usage_estimator": "cl100k_base"
                    if str(row.get("usage_basis") or "").startswith("estimated") else None,
                }
            )
    return raw_calls


IMPORTERS = {
    "jsonl": import_jsonl,
    "otlp": import_otlp,
    "langfuse": import_langfuse,
    "parquet": import_parquet,
}


def import_store(inputs: dict, app_config_path=None, notes=None) -> dict:
    """Run the requested importers and assemble the canonical store.

    ``inputs`` maps importer kind ("jsonl", "otlp", "langfuse", "parquet") to a
    list of paths. Every call is imported exactly once, redacted at creation.
    """
    app_config = None
    if app_config_path:
        with open(app_config_path, "r", encoding="utf-8") as stream:
            app_config = json.load(stream)
    calls = []
    source_files = []
    sequence = 0
    for kind, paths in inputs.items():
        importer = IMPORTERS[kind]
        for path in paths:
            raw_calls = importer(path)
            for raw in raw_calls:
                raw["call_index"] = raw.get("call_index", sequence)
                sequence += 1
                calls.append(make_call(raw, kind, str(path)))
            source_files.append({"importer": kind, "path": str(path), "records": len(raw_calls)})
    calls.sort(key=lambda call: (call["trace_id"], call["call_index"], call["call_id"]))
    all_notes = [
        "All message content was redacted at import; redacted content is the only content stored, replayed, or reported.",
    ] + list(notes or [])
    if app_config and isinstance(app_config, dict):
        all_notes.append(
            "App configuration supplies prompts and tool definitions; it is descriptive, not evidence of behavior."
        )
    return build_store(calls, app_config=app_config, source_files=source_files, notes=all_notes)


# ---------------------------------------------------------------------------
# Evidence, TF-IDF, and provisional workflow grouping
# ---------------------------------------------------------------------------

STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "you", "your", "are", "was", "were",
    "can", "could", "would", "should", "have", "has", "had", "not", "but", "all",
    "please", "need", "want", "from", "into", "about", "there", "their", "them",
    "they", "what", "when", "where", "which", "who", "how", "why", "will", "just",
    "get", "got", "does", "did", "done", "any", "our", "out", "too", "also", "very",
}


def tokenize(text: str) -> list:
    return [
        token for token in re.findall(r"[a-z][a-z0-9_]{2,}", (text or "").lower())
        if token not in STOPWORDS and len(token) < 32
    ]


class TfidfIndex:
    """Small deterministic TF-IDF + cosine similarity index (pure stdlib)."""

    def __init__(self):
        self.idf = {}
        self.vectors = {}

    def fit(self, documents: list) -> "TfidfIndex":
        document_frequency = defaultdict(int)
        for document in documents:
            for token in set(tokenize(document)):
                document_frequency[token] += 1
        total = max(1, len(documents))
        self.idf = {
            token: math.log((1 + total) / (1 + count)) + 1.0
            for token, count in document_frequency.items()
        }
        self.vectors = {index: self.vector(document) for index, document in enumerate(documents)}
        return self

    def vector(self, text: str) -> dict:
        counts = defaultdict(float)
        for token in tokenize(text):
            counts[token] += 1.0
        vector = {token: (1.0 + math.log(count)) * self.idf.get(token, 1.0) for token, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in vector.values()))
        if norm > 0:
            vector = {token: value / norm for token, value in vector.items()}
        return vector

    @staticmethod
    def cosine(a: dict, b: dict) -> float:
        if len(b) < len(a):
            a, b = b, a
        return sum(value * b.get(token, 0.0) for token, value in a.items())

    def similarity_to_text(self, vector: dict, text: str) -> float:
        return self.cosine(vector, self.vector(text))


def tool_jaccard(a: list, b: list) -> float:
    set_a, set_b = set(a or []), set(b or [])
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def trace_evidence_level(trace: dict) -> str:
    if trace["user_requests"]:
        return "user_request"
    if trace["tool_names"]:
        return "tool_call"
    return "assistant_only"


def task_document(trace: dict) -> str:
    parts = list(trace["user_requests"])
    if not parts:
        parts.append(" ".join(trace["tool_names"]))
    return " ".join(parts)


def group_workflows(store: dict, threshold: float = 0.34) -> dict:
    """Provisionally group traces into workflows.

    Similarity = 0.65 * TF-IDF cosine over task text + 0.35 * tool-set Jaccard
    (text-only when neither side has tools). Greedy, deterministic assignment.
    Every output field that a reviewer might correct lives in the workflow map,
    and every cluster carries confidence and uncertainty signals.
    """
    traces = store["traces"]
    calls_by_id = {call["call_id"]: call for call in store["calls"]}
    documents = [task_document(trace) for trace in traces]
    index = TfidfIndex().fit(documents)
    vectors = [index.vectors[i] for i in range(len(traces))]

    clusters = []  # each: {"members": [indices], "centroid_terms": {}}
    for position, trace in enumerate(traces):
        best_cluster, best_score = None, 0.0
        for cluster in clusters:
            representative = max(
                index.cosine(vectors[position], vectors[member]) for member in cluster["members"]
            )
            member = traces[cluster["members"][0]]
            tool_score = tool_jaccard(trace["tool_names"], member["tool_names"])
            if not trace["tool_names"] and not member["tool_names"]:
                score = representative
            else:
                score = 0.65 * representative + 0.35 * tool_score
            if score > best_score:
                best_cluster, best_score = cluster, score
        if best_cluster is not None and best_score >= threshold:
            best_cluster["members"].append(position)
        else:
            clusters.append({"members": [position], "seed_score": best_score})

    workflows = []
    total_traces = max(1, len(traces))
    for cluster_position, cluster in enumerate(clusters):
        members = [traces[i] for i in cluster["members"]]
        member_vectors = [vectors[i] for i in cluster["members"]]
        tool_counter = defaultdict(int)
        for trace in members:
            for name in trace["tool_names"]:
                tool_counter[name] += 1
        term_mass = defaultdict(float)
        for vector in member_vectors:
            for token, value in vector.items():
                term_mass[token] += value
        top_terms = [term for term, _ in sorted(term_mass.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        top_tools = [name for name, _ in sorted(tool_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        parts = []
        if top_terms:
            parts.append(" ".join(top_terms).title())
        if top_tools:
            parts.append(top_tools[0])
        name = " — ".join(parts) if parts else f"Unlabeled activity {cluster_position + 1}"
        name = name[:80]

        usage = merge_usage(*[trace["usage"] for trace in members])
        call_count = sum(trace["call_count"] for trace in members)
        error_traces = sum(1 for trace in members if trace["error_count"])
        evidence_requests = []
        for trace in members:
            for request in trace["user_requests"]:
                evidence_requests.append({"trace_id": trace["trace_id"], "text": excerpt(request, EVIDENCE_CAP)})
        evidence_requests = evidence_requests[:5]
        example_trace_ids = [trace["trace_id"] for trace in members[:5]]

        sizes = [len(members)]
        uncertainties = []
        evidence_level = trace_evidence_level(members[0]) if len(members) == 1 else (
            "user_request" if any(trace["user_requests"] for trace in members) else "tool_call"
        )
        if len(members) == 1:
            uncertainties.append("single_trace_cluster")
        if evidence_level != "user_request":
            uncertainties.append("no_user_request_evidence")
        if error_traces:
            uncertainties.append("contains_failed_calls")
        intra = []
        if len(member_vectors) > 1:
            for i in range(len(member_vectors)):
                for j in range(i + 1, len(member_vectors)):
                    intra.append(index.cosine(member_vectors[i], member_vectors[j]))
            mean_intra = sum(intra) / len(intra)
            if mean_intra < 0.2:
                uncertainties.append("low_internal_similarity")
        else:
            mean_intra = 1.0
        if len(members) >= 2 and evidence_level == "user_request" and mean_intra >= 0.2:
            confidence = "high"
        elif len(members) >= 2 or evidence_level == "user_request":
            confidence = "medium"
        else:
            confidence = "low"

        workflows.append(
            {
                "id": f"wf_{cluster_position + 1:03d}",
                "name": name,
                "description": (
                    f"Provisional group of {len(members)} trace(s) around terms: "
                    f"{', '.join(top_terms) if top_terms else '(no dominant terms)'}"
                    + (f"; tools: {', '.join(top_tools)}" if top_tools else "")
                    + "."
                ),
                "trace_ids": sorted(trace["trace_id"] for trace in members),
                "tool_names": sorted(tool_counter),
                "evidence": {"example_trace_ids": example_trace_ids, "user_requests": evidence_requests},
                "confidence": confidence,
                "uncertainty": uncertainties,
                "evidence_level": evidence_level,
                "label_source": "heuristic",
                "edited": False,
                "stats": {
                    "traces": len(members),
                    "calls": call_count,
                    "error_traces": error_traces,
                    "usage": usage,
                    "share_of_traces": round(len(members) / total_traces, 4),
                    "representative_latency_ms": _representative_latency(members, calls_by_id),
                },
            }
        )
    assigned = set()
    for workflow in workflows:
        assigned.update(workflow["trace_ids"])
    unassigned = sorted(trace["trace_id"] for trace in traces if trace["trace_id"] not in assigned)
    return {
        "workflow_map_version": WORKFLOW_MAP_VERSION,
        "params": {"similarity_threshold": threshold},
        "workflows": workflows,
        "unassigned_trace_ids": unassigned,
        "notes": [
            "Workflow labels and similarity scores are triage aids, not measured accuracy.",
            "Names are derived from dominant TF-IDF terms and tool names; a reviewer (human or agent) should correct them.",
        ],
    }


def _representative_latency(members: list, calls_by_id: dict):
    values = [trace["latency_ms_max"] for trace in members if trace["latency_ms_max"] is not None]
    if not values:
        for trace in members:
            for call_id in trace["call_ids"]:
                latency = calls_by_id[call_id]["latency_ms"]
                if latency is not None:
                    values.append(latency)
    return max(values) if values else None


def save_workflows(workflow_map: dict, path) -> None:
    atomic_write_json(path, workflow_map)


def load_workflows(path) -> dict:
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def apply_edits(workflow_map: dict, previous: dict) -> dict:
    """Re-run grouping while preserving reviewer edits.

    A workflow the reviewer marked ``edited`` keeps its id, name, description,
    confidence, and explicit ``trace_ids``; those traces are removed from the
    automatically regenerated groups. Merges and splits are expressed by
    editing ``trace_ids`` of two workflows. Trace ids that no longer exist in
    the store are dropped.
    """
    if not previous:
        return workflow_map
    previous_edited = [wf for wf in previous.get("workflows", []) if wf.get("edited")]
    if not previous_edited:
        return workflow_map
    known_trace_ids = set(workflow_map.get("unassigned_trace_ids") or [])
    for workflow in workflow_map["workflows"]:
        known_trace_ids.update(workflow["trace_ids"])
    reserved_ids = set()
    preserved = []
    for workflow in previous_edited:
        trace_ids = [tid for tid in workflow.get("trace_ids", []) if tid in known_trace_ids]
        if not trace_ids:
            continue
        reserved_ids.update(trace_ids)
        preserved.append({**workflow, "trace_ids": sorted(trace_ids)})
    if not preserved:
        return workflow_map
    for workflow in workflow_map["workflows"]:
        workflow["trace_ids"] = [tid for tid in workflow["trace_ids"] if tid not in reserved_ids]
    workflow_map["workflows"] = [wf for wf in workflow_map["workflows"] if wf["trace_ids"]] + preserved
    workflow_map["unassigned_trace_ids"] = [
        tid for tid in workflow_map["unassigned_trace_ids"] if tid not in reserved_ids
    ]
    for position, workflow in enumerate(workflow_map["workflows"], start=1):
        workflow["id"] = f"wf_{position:03d}"
    workflow_map.setdefault("notes", []).append(
        "Reviewer edits were preserved from the previous workflow map on this run."
    )
    return workflow_map


# ---------------------------------------------------------------------------
# Session profiling — dig through the store and surface everything inside it
# ---------------------------------------------------------------------------


def _json_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def infer_tool_schema(observations: list) -> dict:
    """Infer a JSON schema for a tool from its observed argument dicts.

    The result is an inference from recorded behavior — a starting point for
    the reviewer, not a recovered ground truth. Keys present in at least 80%
    of observed calls are marked required.
    """
    observations = [obs for obs in observations if isinstance(obs, dict)]
    if not observations:
        return {"type": "object", "properties": {}, "inferred_from": 0}
    total = len(observations)
    keys = {}
    for obs in observations:
        for key, value in obs.items():
            entry = keys.setdefault(key, {"types": set(), "count": 0})
            entry["count"] += 1
            entry["types"].add(_json_type(value))
    properties = {}
    required = []
    for key, entry in sorted(keys.items()):
        types = entry["types"] - {"unknown"}
        if len(types) == 1:
            property_type = next(iter(types))
        elif "string" in types or not types:
            property_type = "string"
        else:
            property_type = sorted(types)[0]
        properties[key] = {"type": property_type}
        if entry["count"] >= 0.8 * total:
            required.append(key)
    schema = {"type": "object", "properties": properties, "inferred_from": total}
    if required:
        schema["required"] = required
    return schema


def build_profile(store: dict) -> dict:
    """Dig through every session and surface: system prompts, a tool inventory
    with schemas inferred from observed calls, provider/model mix, error
    samples, and usage by provenance. Everything inside was redacted at
    import; excerpts stay capped."""
    from collections import Counter, defaultdict

    tool_observations = defaultdict(list)
    tool_call_counts = Counter()
    tool_traces = defaultdict(set)
    system_prompts = Counter()
    provider_models = Counter()
    error_samples = []
    calls_with_errors = 0

    for call in store["calls"]:
        provider_models[(call["provider"], call["model"])] += 1
        response = call["response"]
        if response["error"]:
            calls_with_errors += 1
            if len(error_samples) < 5:
                error_samples.append({"trace_id": call["trace_id"], "error": response["error"]})
        for message in call["request"]["messages"]:
            if message.get("role") == "system" and message.get("content"):
                system_prompts[message["content"]] += 1
        for tool_call in response["tool_calls"]:
            name = tool_call.get("name", "")
            if not name:
                continue
            tool_call_counts[name] += 1
            tool_observations[name].append(tool_call.get("arguments") or {})

    # Which traces use each tool.
    for trace in store["traces"]:
        for name in trace["tool_names"]:
            tool_traces[name].add(trace["trace_id"])

    tools = []
    for name, count in sorted(tool_call_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        observations = tool_observations[name]
        examples = []
        for obs in observations[:3]:
            examples.append(json.dumps(redact_value(obs), sort_keys=True)[:300])
        tools.append(
            {
                "name": name,
                "proposals": count,
                "traces": len(tool_traces[name]),
                "schema_inferred": infer_tool_schema(observations),
                "example_arguments": examples,
            }
        )

    # Also surface tools declared in the request but never proposed.
    for trace in store["traces"]:
        for name in trace["tool_names"]:
            if name not in tool_call_counts:
                tool_call_counts[name] = 0

    prompts = [
        {"excerpt": excerpt(prompt, 600), "calls_using": count}
        for prompt, count in system_prompts.most_common(5)
    ]

    providers = [
        {"provider": provider, "model": model, "calls": count}
        for (provider, model), count in provider_models.most_common(8)
    ]

    # Session origins — the cloud/local split, when the source recorded it.
    origin_counts = Counter()
    titled_sessions = []
    for trace in store["traces"]:
        origin = trace.get("origin")
        if origin:
            origin_counts[origin] += 1
        if trace.get("title"):
            titled_sessions.append(
                {"trace_id": trace["trace_id"], "title": excerpt(trace["title"], 120),
                 "origin": trace.get("origin"), "calls": trace["call_count"]}
            )
    titled_sessions = titled_sessions[:8]

    primary = providers[0] if providers else None
    inferred_config = {
        "name": "inferred-from-traces",
        "inferred": True,
        "prompts": [
            {"id": f"system_inferred_{position}", "role": "system", "template": prompt["excerpt"]}
            for position, prompt in enumerate(prompts[:1])
        ],
        "tools": [
            {
                "name": tool["name"],
                "description": "(inferred from observed calls; edit before use)",
                "parameters": {
                    key: value for key, value in tool["schema_inferred"].items()
                },
            }
            for tool in tools
        ],
        "provider": (
            {"provider": primary["provider"], "model": primary["model"], "base_url": None}
            if primary else None
        ),
    }

    sessions = len(store.get("sessions") or [])
    profile = {
        "profile_version": 1,
        "totals": {
            "traces": store["totals"]["traces"],
            "calls": store["totals"]["calls"],
            "sessions": sessions,
            "calls_with_errors": calls_with_errors,
        },
        "usage": store["totals"]["usage"],
        "providers": providers,
        "session_origins": dict(origin_counts),
        "named_sessions": titled_sessions,
        "system_prompts": prompts,
        "tools": tools,
        "error_samples": error_samples,
        "notes": [
            "Tool schemas are inferred from observed arguments; a reviewer should confirm them before reuse.",
            "All content was redacted at import; excerpts are capped.",
        ],
    }
    return {"profile": profile, "app_config_inferred": inferred_config}


# ---------------------------------------------------------------------------
# Replay-case selection and default success checks
# ---------------------------------------------------------------------------

ORIGIN_PRIORITY = ("failed", "risky", "costly", "common")


def _case_usage_value(usage: dict, key: str):
    """Prefer recorded usage for budget math; fall back to estimated; else None."""
    for basis in ("recorded", "estimated"):
        bucket = (usage or {}).get(basis) or {}
        if bucket.get(f"{key}_tokens_known"):
            return bucket[f"{key}_tokens_known"]
    return None


def _seed_call(store: dict, trace: dict):
    """Pick the call whose response best represents the trace outcome.

    Preference: last call with a non-error response, then last call with any
    response, then simply the last call (error-only traces replay their error).
    """
    calls_by_id = {call["call_id"]: call for call in store["calls"]}
    members = [calls_by_id[cid] for cid in trace["call_ids"] if cid in calls_by_id]
    if not members:
        return store["calls"][0] if store["calls"] else None
    with_response = [call for call in members if call["response"]["text"] or call["response"]["tool_calls"]]
    without_error = [call for call in with_response if not call["response"]["error"]]
    if without_error:
        return without_error[-1]
    if with_response:
        return with_response[-1]
    return members[-1]


def default_checks(case: dict) -> list:
    """Deterministic default checks derived from reference behavior.

    Defaults are deliberately conservative: tool presence, no error, and loose
    latency/usage budgets. They are starting points for a reviewer to edit —
    a historical model response alone is never treated as proof of success.
    """
    checks = [{"type": "no_error", "required": True, "source": "default"}]
    historical_tools = [call["name"] for call in case["historical"]["tool_calls"] if call["name"]]
    for name in sorted(set(historical_tools)):
        checks.append({"type": "tool_called", "name": name, "required": True, "source": "default"})
    historical_text = case["historical"]["text"]
    if historical_text and len(historical_text) > 40:
        checks.append(
            {"type": "output_min_chars", "value": max(20, int(len(historical_text) * 0.25)),
             "required": False, "source": "default"}
        )
    latency = case["historical"].get("latency_ms")
    if latency:
        checks.append(
            {"type": "latency_ms_max", "value": int(max(5000, latency * 3)),
             "required": False, "source": "default"}
        )
    output_tokens = _case_usage_value(case["historical"].get("usage") or {}, "output")
    if output_tokens:
        checks.append(
            {"type": "usage_output_max", "value": int(max(256, output_tokens * 3)),
             "required": False, "source": "default"}
        )
    return checks


def select_cases(store: dict, workflow_map: dict, max_cases: int = 24) -> dict:
    """Select representative replay cases across workflows.

    Coverage goals from the plan: common, costly, risky, and failed workflows,
    plus at least one case from traces outside any confident workflow when they
    exist. One case per trace maximum; per-workflow cap keeps the set small.
    """
    traces_by_id = {trace["trace_id"]: trace for trace in store["traces"]}
    cases = []
    used_traces = set()
    per_workflow_cap = 2

    def base_usage(trace):
        return trace["usage"]

    def make_case(trace, workflow_id, origins):
        seed = _seed_call(store, trace)
        if seed is None:
            return None
        # Tool behavior belongs to the whole trace, not just the final answer:
        # merge every proposed tool call across the trace's calls, keeping the
        # first proposal per tool name.
        calls_by_id = {call["call_id"]: call for call in store["calls"]}
        merged_tools = []
        seen_tool_names = set()
        for call_id in trace["call_ids"]:
            call = calls_by_id.get(call_id)
            if not call:
                continue
            for tool_call in call["response"]["tool_calls"]:
                name = tool_call.get("name", "")
                if name and name not in seen_tool_names:
                    seen_tool_names.add(name)
                    merged_tools.append({"name": name, "arguments": tool_call.get("arguments") or {}})
        # Recorded tool fixtures: what the business tool actually returned in
        # history (paired proposal -> following call's tool-result messages).
        # These let a chained replay complete the workflow offline without
        # ever executing a real business tool.
        tool_fixtures = {}
        members = [calls_by_id[cid] for cid in trace["call_ids"] if cid in calls_by_id]
        for position, call in enumerate(members[:-1]):
            proposed = [tc.get("name", "") for tc in call["response"]["tool_calls"] if tc.get("name")]
            if not proposed:
                continue
            followup = members[position + 1]
            results = [
                message.get("content") for message in followup["request"]["messages"]
                if message.get("role") == "tool" and message.get("content")
            ]
            for name, result in zip(proposed, results):
                if name and result and name not in tool_fixtures:
                    tool_fixtures[name] = result[:1200]
        historical = {
            "text": seed["response"]["text"],
            "tool_calls": merged_tools,
            "finish_reason": seed["response"]["finish_reason"],
            "error": seed["response"]["error"],
            "usage": dict(seed["usage"]),
            "latency_ms": seed["latency_ms"],
            "model": seed["model"],
            "call_id": seed["call_id"],
            "usage_note": (
                "Reference behavior captured from history. It is what the current "
                "provider did, not proof of what success looks like."
            ),
        }
        case = {
            "case_id": f"case_{len(cases) + 1:03d}",
            "workflow_id": workflow_id,
            "trace_id": trace["trace_id"],
            "origins": origins,
            "request": seed["request"],
            "historical": historical,
        }
        if tool_fixtures:
            case["tool_fixtures"] = tool_fixtures
        case["checks"] = default_checks(case)
        case["notes"] = [
            "The historical response is reference behavior, not a success guarantee.",
            "Tool calls proposed by the target are captured and compared, never executed.",
        ]
        if tool_fixtures:
            case["notes"].append(
                "Recorded tool fixtures are attached (tool_fixtures). A chained replay "
                "(replay --chain) can use them to complete the workflow offline — the "
                "harness never executes a real business tool."
            )
        return case

    def add_candidate(trace, workflow_id, origin):
        if trace["trace_id"] in used_traces:
            for existing in cases:
                if existing["trace_id"] == trace["trace_id"] and origin not in existing["origins"]:
                    existing["origins"].append(origin)
                    existing["origins"].sort(key=ORIGIN_PRIORITY.index)
                    return
            return
        case = make_case(trace, workflow_id, [origin])
        if case is not None:
            cases.append(case)
            used_traces.add(trace["trace_id"])

    for workflow in workflow_map["workflows"]:
        members = [traces_by_id[tid] for tid in workflow["trace_ids"] if tid in traces_by_id]
        if not members:
            continue
        picks = []
        failed = [trace for trace in members if trace["error_count"]]
        risky = sorted(members, key=lambda trace: (-len(trace["tool_names"]), trace["trace_id"]))
        costly = sorted(
            members,
            key=lambda trace: (-(_case_usage_value(base_usage(trace), "input") or 0), trace["trace_id"]),
        )
        common = sorted(members, key=lambda trace: (-trace["call_count"], trace["trace_id"]))
        if failed:
            picks.append((failed[0], "failed"))
        if risky:
            picks.append((risky[0], "risky"))
        if costly:
            picks.append((costly[0], "costly"))
        if common:
            picks.append((common[0], "common"))
        added = 0
        for trace, origin in picks:
            if added >= per_workflow_cap:
                break
            existed_before = trace["trace_id"] in used_traces
            add_candidate(trace, workflow["id"], origin)
            # The cap limits distinct cases per workflow, not the origins a
            # single trace collects when every pick lands on the same trace.
            if not existed_before:
                added += 1

    unassigned_ids = list(workflow_map.get("unassigned_trace_ids") or [])
    if unassigned_ids:
        trace = traces_by_id.get(unassigned_ids[0])
        if trace:
            add_candidate(trace, "unassigned", "common")

    # Deterministic ordering: workflow id, then case id; hard cap with failed
    # cases kept preferentially so the small set still sees failures.
    cases.sort(key=lambda case: (case["workflow_id"], case["case_id"]))
    if len(cases) > max_cases:
        failures = [case for case in cases if "failed" in case["origins"]]
        others = [case for case in cases if "failed" not in case["origins"]]
        keep = failures[:max_cases] if len(failures) >= max_cases else failures + others[: max_cases - len(failures)]
        cases = sorted(keep, key=lambda case: (case["workflow_id"], case["case_id"]))
    for position, case in enumerate(cases, start=1):
        case["case_id"] = f"case_{position:03d}"
    return {
        "cases_version": CASES_VERSION,
        "params": {"max_cases": max_cases, "per_workflow_cap": per_workflow_cap},
        "cases": cases,
        "notes": [
            "Checks are defaults generated from reference behavior. Edit cases.json to add explicit success checks where outcomes matter.",
            "Deterministic checks only. An LLM judge, if added by hand, is a fallible measurement tool, not ground truth.",
        ],
    }


def save_cases(cases_doc: dict, path) -> None:
    atomic_write_json(path, cases_doc)


def load_cases(path) -> dict:
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)
