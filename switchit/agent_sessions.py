"""Importers for coding-agent session folders — traces people already have.

Codex, Claude Code, and OpenCode record full sessions locally: model calls,
tool proposals, tool results, and token usage. Nobody thinks of these as
"telemetry", but that is exactly what they are, so this module turns them into
canonical SwitchIt calls with zero instrumentation:

- Codex:       ~/.codex/sessions/**/*.jsonl   (rollout files)
- Claude Code: ~/.claude/projects/**/*.jsonl  (session transcripts)
- OpenCode:    ~/.local/share/opencode/opencode.db (message/part tables)

Parsing is tolerant by design: unknown line types, missing fields, and
schema drift are skipped, never crashes. Everything goes through the same
redaction pipeline as every other importer, and usage stays provenance-labeled
(`recorded` when the agent reported tokens, `unknown` otherwise).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REDACT_MARKER = "[REDACTED]"


def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _open_text(path: Path):
    """Open a text file, transparently decompressing .zst (Codex compresses
    older rollouts). Uses the stdlib zstd on Python 3.14+, else the optional
    `zstandard` package."""
    if path.suffix == ".zst":
        try:
            from compression import zstd  # Python 3.14+
        except ImportError:
            try:
                import zstandard

                return zstandard.open(path, "rt", encoding="utf-8")
            except ImportError as error:
                raise ImportError(
                    f"{path} is zstd-compressed; reading it needs Python 3.14+ "
                    "or the optional zstandard package (pip install zstandard)"
                ) from error
        return zstd.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _iter_jsonl(path: Path):
    try:
        with _open_text(path) as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def _content_text(content) -> str:
    """Flatten Anthropic/OpenAI-style content blocks to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                for key in ("text", "thinking", "input_text", "output_text"):
                    if isinstance(block.get(key), str):
                        parts.append(block[key])
                        break
    return "\n".join(p for p in parts if p)


def _usage_recorded(usage) -> dict:
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None and output_tokens is None:
        return None
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "basis": "recorded"}


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def import_claude_code(path) -> list:
    """Parse one Claude Code session transcript (.jsonl)."""
    raw_calls = []
    session_id = Path(path).stem
    pending_tools = []  # tool_use proposals awaiting their results
    last_time = None
    for line in _iter_jsonl(Path(path)):
        line_type = line.get("type")
        if line_type not in ("user", "assistant"):
            continue
        session_id = line.get("sessionId") or session_id
        message = line.get("message") or {}
        role = message.get("role") or line_type
        timestamp = _parse_time(line.get("timestamp"))
        if role == "assistant":
            blocks = message.get("content")
            if isinstance(blocks, str):
                text, tool_blocks = blocks, []
            else:
                text = _content_text(
                    [b for b in (blocks or []) if isinstance(b, dict) and b.get("type") == "text"]
                )
                tool_blocks = [b for b in (blocks or [])
                               if isinstance(b, dict) and b.get("type") == "tool_use"]
            tool_calls = [
                {"name": block.get("name", ""), "arguments": block.get("input") or {}}
                for block in tool_blocks
            ]
            pending_tools = [call["name"] for call in tool_calls if call["name"]]
            raw_calls.append(
                {
                    "trace_id": f"claude-code:{session_id}",
                    "call_id": message.get("id"),
                    "session_id": session_id,
                    "call_index": len(raw_calls),
                    "provider": "anthropic",
                    "model": message.get("model"),
                    "request": {"messages": []},
                    "response": {
                        "text": text,
                        "tool_calls": tool_calls,
                        "finish_reason": message.get("stop_reason"),
                    },
                    "latency_ms": int((timestamp - last_time) * 1000)
                    if timestamp and last_time and timestamp > last_time else None,
                    "usage": _usage_recorded(message.get("usage")),
                }
            )
            last_time = timestamp or last_time
        elif role == "user":
            blocks = message.get("content")
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result_text = _content_text(block.get("content"))
                        if result_text:
                            raw_calls.append(_tool_result_call(
                                f"claude-code:{session_id}", session_id, len(raw_calls),
                                pending_tools.pop(0) if pending_tools else None, result_text,
                            ))
            elif isinstance(blocks, str) and blocks.strip():
                raw_calls.append(
                    {
                        "trace_id": f"claude-code:{session_id}",
                        "call_id": f"{session_id}:user:{len(raw_calls)}",
                        "session_id": session_id,
                        "call_index": len(raw_calls),
                        "provider": "anthropic",
                        "model": "user-turn",
                        "request": {"messages": [{"role": "user", "content": blocks}]},
                        "response": None,
                        "usage": None,
                    }
                )
    return raw_calls


def _tool_result_call(trace_id, session_id, call_index, tool_name, result_text):
    return {
        "trace_id": trace_id,
        "call_id": f"{session_id}:tool:{call_index}",
        "session_id": session_id,
        "call_index": call_index,
        "provider": "anthropic",
        "model": "tool-result",
        "request": {"messages": [{"role": "tool", "content": result_text,
                                  "name": tool_name or "unknown_tool"}]},
        "response": None,
        "usage": None,
        "usage_basis": None,
    }


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


def import_codex(path) -> list:
    """Parse one Codex rollout file (.jsonl)."""
    raw_calls = []
    session_id = None
    system_text = ""
    last_assistant_usage = None
    index = 0
    for line in _iter_jsonl(Path(path)):
        line_type = line.get("type")
        payload = line.get("payload") or {}
        if line_type == "session_meta":
            session_id = payload.get("session_id") or payload.get("id") or Path(path).stem
            instructions = payload.get("base_instructions")
            if isinstance(instructions, dict):
                instructions = instructions.get("text")
            system_text = instructions or ""
            continue
        if line_type == "event_msg":
            info = payload.get("info") or {}
            usage = info.get("total_token_usage") or info.get("token_usage")
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", usage.get("input"))
                output_tokens = usage.get("output_tokens", usage.get("output"))
                if input_tokens is not None or output_tokens is not None:
                    last_assistant_usage = {
                        "input_tokens": input_tokens, "output_tokens": output_tokens,
                        "basis": "recorded",
                    }
            continue
        if line_type != "response_item":
            continue
        payload_type = payload.get("type")
        trace_id = f"codex:{session_id or Path(path).stem}"
        if payload_type == "message":
            role = payload.get("role") or "user"
            text = _content_text(payload.get("content"))
            if not text:
                continue
            if role == "assistant":
                raw_calls.append(
                    {
                        "trace_id": trace_id,
                        "call_id": f"{session_id}:assistant:{index}",
                        "session_id": str(session_id) if session_id else None,
                        "call_index": index,
                        "provider": "openai",
                        "model": None,
                        "request": {"messages": (
                            [{"role": "system", "content": system_text}]
                            if system_text and not any(
                                c["response"] and c["response"].get("text") for c in raw_calls
                            )
                            else []
                        )},
                        "response": {"text": text, "tool_calls": [], "finish_reason": "stop"},
                        "usage": last_assistant_usage,
                    }
                )
                index += 1
                last_assistant_usage = None
            elif role == "user" and raw_calls is not None:
                # Record the user turn as its own lightweight call so requests
                # stay linked to the answers that followed them.
                raw_calls.append(
                    {
                        "trace_id": trace_id,
                        "call_id": f"{session_id}:user:{index}",
                        "session_id": str(session_id) if session_id else None,
                        "call_index": index,
                        "provider": "openai",
                        "model": "user-turn",
                        "request": {"messages": [{"role": "user", "content": text}]},
                        "response": None,
                        "usage": None,
                    }
                )
                index += 1
        elif payload_type == "function_call":
            raw_calls.append(
                {
                    "trace_id": trace_id,
                    "call_id": payload.get("call_id") or f"{session_id}:fn:{index}",
                    "session_id": str(session_id) if session_id else None,
                    "call_index": index,
                    "provider": "openai",
                    "model": "tool-call",
                    "request": {"messages": []},
                    "response": {"text": "", "finish_reason": "tool_calls",
                                 "tool_calls": [{"name": payload.get("name", ""),
                                                 "arguments": _safe_json(payload.get("arguments"))}]},
                    "usage": None,
                }
            )
            index += 1
        elif payload_type == "function_call_output":
            raw_calls.append(
                _tool_result_call(trace_id, str(session_id), index, None,
                                  str(payload.get("output") or ""))
            )
            index += 1
    return raw_calls


def _safe_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"_raw": value[:400]}
        except ValueError:
            return {"_raw": value[:400]}
    return {}


def import_cursor(path) -> list:
    """Parse one Cursor chat (a folder containing store.db + meta.json).

    Blobs are JSON messages {role, content}; usage is not reported, so these
    calls land in the `unknown` provenance bucket. Tool calls appear only in
    agentic chats and are extracted tolerantly from whatever block shape the
    version used.
    """
    path = Path(path)
    store_db = path if path.name == "store.db" else path / "store.db"
    if not store_db.exists():
        return []
    chat_id = store_db.parent.name
    trace_id = f"cursor:{chat_id}"
    raw_calls = []
    system_text = None
    index = 0
    connection = sqlite3.connect(f"file:{store_db}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT data FROM blobs ORDER BY rowid")
        for (blob,) in rows:
            try:
                message = json.loads(blob)
            except (ValueError, TypeError):
                continue
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                system_text = _content_text(content)
                continue
            if role == "assistant":
                text, tool_calls = _cursor_assistant_content(content)
                request_messages = []
                if system_text:
                    request_messages.append({"role": "system", "content": system_text})
                    system_text = None  # attach once, to the first assistant call
                raw_calls.append(
                    {
                        "trace_id": trace_id,
                        "call_id": message.get("id") or f"{chat_id}:assistant:{index}",
                        "session_id": chat_id,
                        "call_index": index,
                        "provider": "cursor",
                        "model": message.get("model") or None,
                        "request": {"messages": request_messages},
                        "response": {
                            "text": text,
                            "tool_calls": tool_calls,
                            "finish_reason": "stop" if not tool_calls else "tool_calls",
                        },
                        "usage": None,
                    }
                )
                index += 1
            elif role == "user":
                text = _content_text(content)
                if text.strip():
                    raw_calls.append(
                        {
                            "trace_id": trace_id,
                            "call_id": f"{chat_id}:user:{index}",
                            "session_id": chat_id,
                            "call_index": index,
                            "provider": "cursor",
                            "model": "user-turn",
                            "request": {"messages": [{"role": "user", "content": text}]},
                            "response": None,
                            "usage": None,
                        }
                    )
                    index += 1
    except sqlite3.Error:
        return raw_calls
    finally:
        connection.close()
    return raw_calls


def _cursor_assistant_content(content) -> tuple:
    """Extract (text, tool_calls) tolerantly from a Cursor assistant blob."""
    tool_calls = []
    if isinstance(content, str):
        return content, tool_calls
    texts = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("text"), str):
            texts.append(block["text"])
        wrapped = block.get("toolCall") or block.get("tool_use") or block.get("function_call")
        candidate = wrapped if isinstance(wrapped, dict) else block
        name = candidate.get("name") or (candidate.get("function") or {}).get("name")
        if name:
            arguments = (candidate.get("arguments") or candidate.get("input")
                         or (candidate.get("function") or {}).get("arguments") or {})
            if isinstance(arguments, str):
                arguments = _safe_json(arguments)
            tool_calls.append({"name": name, "arguments": arguments if isinstance(arguments, dict) else {}})
    return "\n".join(t for t in texts if t), tool_calls





def import_opencode(path) -> list:
    """Parse an OpenCode storage database (message + part tables)."""
    path = Path(path)
    if not path.exists():
        return []
    raw_calls = []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        parts_by_message = defaultdict(list)
        try:
            for row in connection.execute("SELECT message_id, data FROM part"):
                try:
                    parts_by_message[row["message_id"]].append(json.loads(row["data"]))
                except ValueError:
                    continue
        except sqlite3.OperationalError:
            pass  # older storage layouts keep parts inline
        counters = defaultdict(int)
        for row in connection.execute("SELECT id, session_id, data FROM message ORDER BY rowid"):
            try:
                data = json.loads(row["data"])
            except ValueError:
                continue
            role = data.get("role")
            session_id = row["session_id"]
            trace_id = f"opencode:{session_id}"
            parts = parts_by_message.get(row["id"]) or data.get("parts") or []
            if role == "assistant":
                text_parts = [part.get("text", "") for part in parts
                              if isinstance(part, dict) and part.get("type") == "text"]
                tool_calls = [
                    {"name": part.get("tool") or part.get("name", ""),
                     "arguments": (part.get("state") or {}).get("input") or part.get("input") or {}}
                    for part in parts
                    if isinstance(part, dict) and part.get("type") in ("tool", "tool-invocation")
                ]
                usage = data.get("tokens") or {}
                raw_calls.append(
                    {
                        "trace_id": trace_id,
                        "call_id": row["id"],
                        "session_id": session_id,
                        "call_index": counters[session_id],
                        "provider": data.get("providerID") or (data.get("model") or {}).get("providerID") or "opencode",
                        "model": data.get("modelID") or (data.get("model") or {}).get("modelID"),
                        "request": {"messages": []},
                        "response": {
                            "text": "\n".join(t for t in text_parts if t),
                            "tool_calls": tool_calls,
                            "finish_reason": data.get("finish"),
                        },
                        "usage": _usage_recorded(usage) or (
                            {"input_tokens": usage.get("input"), "output_tokens": usage.get("output"),
                             "basis": "recorded"}
                            if usage.get("input") is not None or usage.get("output") is not None else None
                        ),
                    }
                )
                counters[session_id] += 1
            elif role == "user":
                text_parts = [part.get("text", "") for part in parts
                              if isinstance(part, dict) and part.get("type") == "text"]
                text = "\n".join(t for t in text_parts if t)
                if text:
                    raw_calls.append(
                        {
                            "trace_id": trace_id,
                            "call_id": row["id"],
                            "session_id": session_id,
                            "call_index": counters[session_id],
                            "provider": "opencode",
                            "model": "user-turn",
                            "request": {"messages": [{"role": "user", "content": text}]},
                            "response": None,
                            "usage": None,
                        }
                    )
                    counters[session_id] += 1
    finally:
        connection.close()
    return raw_calls


# ---------------------------------------------------------------------------
# Cline (legacy tasks + SDK sessions)
# ---------------------------------------------------------------------------


def import_cline(path) -> list:
    """Parse a Cline store: SDK sessions, legacy task dirs, or one file.

    Shapes (documented in Cline's source, sdk/packages/shared/src/storage):
    - ``<dir>/sessions/<id>/<id>.messages.json`` — SDK session transcripts.
    - ``<dir>/tasks/<id>/api_conversation_history.json`` — legacy Anthropic-style
      conversation arrays, next to ``ui_messages.json`` for usage.
    Both go through the same redaction pipeline as every other importer.
    """
    path = Path(path)
    raw_calls = []
    if path.is_file():
        if path.name.endswith(".messages.json"):
            raw_calls.extend(_cline_sdk_messages_file(path))
        elif path.name == "api_conversation_history.json":
            raw_calls.extend(_cline_legacy_task(path.parent))
        return raw_calls
    if (path / "api_conversation_history.json").exists():
        return _cline_legacy_task(path)
    sessions_dir = path / "data" / "sessions" if (path / "data").exists() else path / "sessions"
    if sessions_dir.exists():
        for messages_file in sorted(sessions_dir.glob("*/*.messages.json")):
            raw_calls.extend(_cline_sdk_messages_file(messages_file))
    if (path / "tasks").exists():
        for task_dir in sorted(candidate for candidate in (path / "tasks").iterdir() if candidate.is_dir()):
            history_file = task_dir / "api_conversation_history.json"
            if history_file.exists():
                raw_calls.extend(_cline_legacy_task(task_dir))
    return raw_calls


def _cline_sdk_messages_file(messages_file: Path) -> list:
    try:
        document = json.loads(messages_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(document, dict):
        return []
    session_id = document.get("sessionId") or messages_file.parent.name
    trace_id = f"cline:{session_id}"
    system_text = document.get("system_prompt") or ""
    raw_calls = []
    system_attached = False
    for message in document.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        text_parts = [b.get("text", "") for b in blocks
                      if isinstance(b, dict) and b.get("type") == "text"]
        tool_calls = [
            {"name": b.get("name", ""), "arguments": b.get("input") or {}}
            for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
        ]
        metrics = message.get("metrics") or {}
        usage = None
        if metrics.get("inputTokens") is not None or metrics.get("outputTokens") is not None:
            usage = {"input_tokens": metrics.get("inputTokens"),
                     "output_tokens": metrics.get("outputTokens"), "basis": "recorded"}
        if role == "assistant":
            request_messages = []
            if system_text and not system_attached:
                request_messages.append({"role": "system", "content": system_text})
                system_attached = True
            raw_calls.append(
                {
                    "trace_id": trace_id,
                    "call_id": message.get("id") or f"{session_id}:a:{len(raw_calls)}",
                    "session_id": session_id,
                    "call_index": len(raw_calls),
                    "provider": (message.get("modelInfo") or {}).get("provider") or "cline",
                    "model": (message.get("modelInfo") or {}).get("id"),
                    "request": {"messages": request_messages},
                    "response": {"text": "\n".join(t for t in text_parts if t),
                                 "tool_calls": tool_calls, "finish_reason": "stop"},
                    "usage": usage,
                }
            )
        elif role == "user":
            tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_results:
                for result in tool_results:
                    result_text = _cline_result_text(result.get("content"))
                    if result_text:
                        raw_calls.append(_tool_result_call(
                            trace_id, session_id, len(raw_calls),
                            result.get("name") or "unknown_tool", result_text,
                        ))
            else:
                text = "\n".join(t for t in text_parts if t)
                if text.strip():
                    raw_calls.append(
                        {
                            "trace_id": trace_id,
                            "call_id": f"{session_id}:u:{len(raw_calls)}",
                            "session_id": session_id,
                            "call_index": len(raw_calls),
                            "provider": "cline",
                            "model": "user-turn",
                            "request": {"messages": [{"role": "user", "content": text}]},
                            "response": None,
                            "usage": None,
                        }
                    )
    return raw_calls


def _cline_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_cline_result_text(item) for item in content)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("output") or "")
    return ""


def _cline_legacy_task(task_dir: Path) -> list:
    history_file = task_dir / "api_conversation_history.json"
    try:
        conversation = json.loads(history_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(conversation, list):
        return []
    task_id = task_dir.name
    trace_id = f"cline:{task_id}"
    # Usage lives in ui_messages.json say/api_req_started entries, in order.
    usage_events = []
    ui_file = task_dir / "ui_messages.json"
    if ui_file.exists():
        try:
            ui_messages = json.loads(ui_file.read_text(encoding="utf-8"))
            for entry in ui_messages or []:
                if not isinstance(entry, dict) or entry.get("say") != "api_req_started":
                    continue
                try:
                    info = json.loads(entry.get("text") or "{}")
                except ValueError:
                    continue
                if info.get("tokensIn") is not None or info.get("tokensOut") is not None:
                    usage_events.append({"input_tokens": info.get("tokensIn"),
                                         "output_tokens": info.get("tokensOut"),
                                         "basis": "recorded"})
        except (ValueError, OSError):
            usage_events = []
    raw_calls = []
    usage_index = 0
    for position, message in enumerate(conversation):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        if role == "assistant":
            tool_calls = [
                {"name": b.get("name", ""), "arguments": b.get("input") or {}}
                for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
            ]
            text = _cline_result_text([b.get("text", "") for b in blocks
                                       if isinstance(b, dict) and b.get("type") == "text"])
            usage = usage_events[usage_index] if usage_index < len(usage_events) else None
            usage_index += 1
            raw_calls.append(
                {
                    "trace_id": trace_id,
                    "call_id": f"{task_id}:a:{position}",
                    "session_id": task_id,
                    "call_index": position,
                    "provider": "cline",
                    "model": None,
                    "request": {"messages": []},
                    "response": {"text": text, "tool_calls": tool_calls, "finish_reason": "stop"},
                    "usage": usage,
                }
            )
        elif role == "user":
            tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_results:
                for result in tool_results:
                    result_text = _cline_result_text(result.get("content"))
                    if result_text:
                        raw_calls.append(_tool_result_call(
                            trace_id, task_id, len(raw_calls),
                            result.get("name") or "unknown_tool", result_text,
                        ))
            else:
                text = _cline_result_text(content if isinstance(content, list)
                                          else [{"type": "text", "text": content or ""}])
                if text.strip():
                    raw_calls.append(
                        {
                            "trace_id": trace_id,
                            "call_id": f"{task_id}:u:{position}",
                            "session_id": task_id,
                            "call_index": position,
                            "provider": "cline",
                            "model": "user-turn",
                            "request": {"messages": [{"role": "user", "content": text}]},
                            "response": None,
                            "usage": None,
                        }
                    )
    return raw_calls


# ---------------------------------------------------------------------------
# Aider (project-local markdown history)
# ---------------------------------------------------------------------------


AIDER_HEADER_PATTERN = re.compile(r"^# aider chat started at (.+)$")


def import_aider(path) -> list:
    """Parse ``.aider.chat.history.md`` — one file holds every session.

    Documented in aider's source (aider/io.py, aider/utils.py): ``# aider chat
    started at`` headers date sessions, ``#### `` lines are user prompts,
    ``> `` blockquotes are tool/system output, bare markdown is the assistant.
    No session ids exist, so one is synthesised per header. No usage, no
    structured tool calls — this is task-level evidence, honestly weaker.
    """
    path = Path(path)
    raw_calls = []
    session_stamp = None
    call_index = 0
    assistant_lines = []
    tool_lines = []

    def flush_assistant():
        nonlocal assistant_lines, call_index
        if session_stamp is not None and assistant_lines:
            raw_calls.append(
                {
                    "trace_id": f"aider:{path}#{session_stamp}",
                    "call_id": f"{session_stamp}:a:{call_index}",
                    "session_id": session_stamp,
                    "call_index": call_index,
                    "provider": "aider",
                    "model": None,
                    "request": {"messages": []},
                    "response": {"text": "\n".join(assistant_lines).strip(),
                                 "tool_calls": [], "finish_reason": "stop"},
                    "usage": None,
                }
            )
            call_index += 1
            assistant_lines = []

    def flush_tool():
        nonlocal tool_lines, call_index
        if session_stamp is not None and tool_lines:
            raw_calls.append(_tool_result_call(
                f"aider:{path}#{session_stamp}", session_stamp, call_index,
                None, "\n".join(tool_lines).strip(),
            ))
            call_index += 1
            tool_lines = []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        header = AIDER_HEADER_PATTERN.match(line.strip())
        if header:
            flush_assistant()
            flush_tool()
            session_stamp = header.group(1).strip()
            call_index = 0
        elif line.startswith("#### "):
            flush_assistant()
            flush_tool()
            if session_stamp is not None:
                prompt = line[5:].rstrip().rstrip("\\").strip()
                if prompt:
                    raw_calls.append(
                        {
                            "trace_id": f"aider:{path}#{session_stamp}",
                            "call_id": f"{session_stamp}:u:{call_index}",
                            "session_id": session_stamp,
                            "call_index": call_index,
                            "provider": "aider",
                            "model": "user-turn",
                            "request": {"messages": [{"role": "user", "content": prompt}]},
                            "response": None,
                            "usage": None,
                        }
                    )
                    call_index += 1
        elif line.startswith("> "):
            flush_assistant()
            tool_lines.append(line[2:].rstrip())
        elif line.strip():
            flush_tool()
            assistant_lines.append(line)
    flush_assistant()
    flush_tool()
    return raw_calls


# ---------------------------------------------------------------------------
# Gemini CLI (session JSONL with $set/$patch update records)
# ---------------------------------------------------------------------------


def import_gemini_cli(path) -> list:
    """Parse one Gemini CLI session transcript (``~/.gemini/tmp/<slug>/chats/*.jsonl``).

    Documented in gemini-cli's source (chatRecordingTypes.ts): a metadata line,
    then message records {id, timestamp, type: user|gemini|info|error, content,
    toolCalls[{id, name, args, result, status}], tokens{input, output}, model},
    plus update records ``$set`` / ``$patch`` applied by id.
    """
    path = Path(path)
    messages_by_id = {}
    order = []
    session_id = path.stem
    kind = "main"
    for record in _iter_jsonl(path):
        if not isinstance(record, dict):
            continue
        if record.get("sessionId") and "id" not in record:
            session_id = record["sessionId"]  # the metadata line
            continue
        if "$set" in record:
            payload = record["$set"] or {}
            if isinstance(payload.get("messages"), list):
                messages_by_id.clear()
                order.clear()
                for message in payload["messages"]:
                    if isinstance(message, dict) and message.get("id"):
                        messages_by_id[message["id"]] = message
                        order.append(message["id"])
            for key in ("sessionId", "kind"):
                if payload.get(key):
                    if key == "sessionId":
                        session_id = payload[key]
                    else:
                        kind = payload[key]
            continue
        if "$patch" in record:
            payload = record["$patch"] or {}
            for update in payload.get("updates") or []:
                if not isinstance(update, dict) or not update.get("id"):
                    continue
                existing = messages_by_id.setdefault(update["id"], {"id": update["id"]})
                if update["id"] not in order:
                    order.append(update["id"])
                for key in ("content", "toolCalls", "tokens", "model", "type", "timestamp"):
                    if update.get(key) is not None:
                        existing[key] = update[key]
            continue
        if "$rewindTo" in record:
            rewind_id = record["$rewindTo"]
            if rewind_id in order:
                position = order.index(rewind_id)
                for dropped in order[position + 1:]:
                    messages_by_id.pop(dropped, None)
                order = order[: position + 1]
            continue
        if record.get("id") is not None:
            message_id = record["id"]
            if message_id in messages_by_id:
                messages_by_id[message_id].update(record)
            else:
                messages_by_id[message_id] = record
                order.append(message_id)

    trace_id = f"gemini-cli:{session_id}"
    raw_calls = []
    for message_id in order:
        message = messages_by_id[message_id]
        message_type = message.get("type")
        if message_type == "user":
            text = _gemini_text(message.get("content"))
            if text.strip():
                raw_calls.append(
                    {
                        "trace_id": trace_id,
                        "call_id": message_id,
                        "session_id": session_id,
                        "call_index": len(raw_calls),
                        "provider": "gemini-cli",
                        "model": "user-turn",
                        "request": {"messages": [{"role": "user", "content": text}]},
                        "response": None,
                        "usage": None,
                    }
                )
        elif message_type == "gemini":
            tokens = message.get("tokens") or {}
            usage = None
            if tokens.get("input") is not None or tokens.get("output") is not None:
                usage = {"input_tokens": tokens.get("input"),
                         "output_tokens": tokens.get("output"), "basis": "recorded"}
            tool_calls = [
                {"name": call.get("name", ""), "arguments": call.get("args") or {}}
                for call in message.get("toolCalls") or []
                if isinstance(call, dict) and call.get("name")
            ]
            raw_calls.append(
                {
                    "trace_id": trace_id,
                    "call_id": message_id,
                    "session_id": session_id,
                    "call_index": len(raw_calls),
                    "provider": "google",
                    "model": message.get("model"),
                    "request": {"messages": []},
                    "response": {
                        "text": _gemini_text(message.get("content")),
                        "tool_calls": tool_calls,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    },
                    "usage": usage,
                }
            )
            # Gemini records each tool result inside the same message; emit
            # them as tool-result calls so chained fixtures can pair them.
            for call in message.get("toolCalls") or []:
                if not isinstance(call, dict) or not call.get("name"):
                    continue
                result = call.get("result")
                result_text = _gemini_text(result)
                if result_text:
                    raw_calls.append(_tool_result_call(
                        trace_id, session_id, len(raw_calls), call["name"], result_text,
                    ))
    return raw_calls


def _gemini_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                response = item.get("functionResponse")
                if isinstance(response, dict):
                    inner = response.get("response")
                    if isinstance(inner, dict) and inner.get("output") is not None:
                        parts.append(str(inner["output"]))
                        continue
                parts.append(str(item.get("text") or item.get("output") or ""))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("output") or "")
    return ""


# ---------------------------------------------------------------------------
# GitHub Copilot CLI (session event log)
# ---------------------------------------------------------------------------


def import_copilot_cli(path) -> list:
    """Parse one Copilot CLI ``events.jsonl`` (``~/.copilot/session-state/<id>/``).

    Documented via the shipped event schema (session-events.schema.json): every
    line is {id, parentId, timestamp, type, data}; a parser reads
    ``session.start`` (cwd), ``system.message`` (system prompt),
    ``user.message`` (prompts), ``assistant.message`` (content, toolRequests,
    model), ``tool.execution_complete`` (results), ``session.usage_record``
    (token usage).
    """
    path = Path(path)
    raw_calls = []
    session_id = path.parent.name if path.parent.name != "session-state" else path.stem
    trace_id = f"copilot-cli:{session_id}"
    system_text = None
    system_attached = False
    pending_tools = {}
    last_usage = None
    for record in _iter_jsonl(path):
        if not isinstance(record, dict):
            continue
        event_type = record.get("type")
        data = record.get("data") or {}
        timestamp = _parse_time(record.get("timestamp"))
        if event_type == "session.start":
            session_id = data.get("sessionId") or session_id
            trace_id = f"copilot-cli:{session_id}"
        elif event_type == "system.message":
            if data.get("content"):
                system_text = _content_text(data.get("content"))
        elif event_type == "user.message":
            text = _content_text(data.get("content"))
            if text.strip():
                raw_calls.append(
                    {
                        "trace_id": trace_id,
                        "call_id": data.get("messageId") or f"{session_id}:u:{len(raw_calls)}",
                        "session_id": session_id,
                        "call_index": len(raw_calls),
                        "provider": "copilot-cli",
                        "model": "user-turn",
                        "request": {"messages": [{"role": "user", "content": text}]},
                        "response": None,
                        "usage": None,
                    }
                )
        elif event_type == "assistant.message":
            requests = [
                {"name": request.get("name", ""),
                 "arguments": request.get("arguments") if isinstance(request.get("arguments"), dict) else {}}
                for request in data.get("toolRequests") or []
                if isinstance(request, dict) and request.get("name")
            ]
            for request in data.get("toolRequests") or []:
                if isinstance(request, dict) and request.get("toolCallId") and request.get("name"):
                    pending_tools[request["toolCallId"]] = request["name"]
            request_messages = []
            if system_text and not system_attached:
                request_messages.append({"role": "system", "content": system_text})
                system_attached = True
            usage = last_usage if last_usage else None
            raw_calls.append(
                {
                    "trace_id": trace_id,
                    "call_id": data.get("messageId") or f"{session_id}:a:{len(raw_calls)}",
                    "session_id": session_id,
                    "call_index": len(raw_calls),
                    "provider": "github-copilot",
                    "model": data.get("model"),
                    "request": {"messages": request_messages},
                    "response": {
                        "text": _content_text(data.get("content")),
                        "tool_calls": requests,
                        "finish_reason": "tool_calls" if requests else "stop",
                    },
                    "usage": usage,
                }
            )
            last_usage = None
        elif event_type == "tool.execution_complete":
            data_tool_id = data.get("toolCallId")
            tool_name = pending_tools.get(data_tool_id) or data.get("toolName") or "unknown_tool"
            result = data.get("result") or {}
            result_text = _content_text(result.get("content") if isinstance(result, dict) else result)
            if result_text:
                raw_calls.append(_tool_result_call(
                    trace_id, session_id, len(raw_calls), tool_name, result_text,
                ))
        elif event_type == "session.usage_record":
            usage = data.get("usage") or {}
            if usage.get("inputTokens") is not None or usage.get("outputTokens") is not None:
                last_usage = {"input_tokens": usage.get("inputTokens"),
                              "output_tokens": usage.get("outputTokens"), "basis": "recorded"}
    return raw_calls


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_VSCODE_GLOBAL_STORAGE_CANDIDATES = [
    Path.home() / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev",
    Path.home() / ".config" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev",
    Path.home() / ".cline" / "data",
]

AGENT_DEFAULTS = {
    "codex": [Path.home() / ".codex" / "sessions"],
    "claude-code": [Path.home() / ".claude" / "projects"],
    "opencode": [
        Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        Path.home() / ".local" / "share" / "opencode" / "storage",
    ],
    "cursor": [Path.home() / ".cursor" / "chats"],
    "cline": _VSCODE_GLOBAL_STORAGE_CANDIDATES,
    "gemini-cli": [Path.home() / ".gemini" / "tmp"],
    "copilot-cli": [Path.home() / ".copilot" / "session-state"],
}

IMPORTERS_BY_AGENT = {
    "codex": import_codex,
    "claude-code": import_claude_code,
    "opencode": import_opencode,
    "cursor": import_cursor,
    "cline": import_cline,
    "gemini-cli": import_gemini_cli,
    "copilot-cli": import_copilot_cli,
}


def discover_agent_sessions(kinds=None, limit: int = 200) -> list:
    """Find existing coding-agent session files on this machine.

    Returns a list of {"agent", "path"} entries, newest-ish first where the
    layout allows. Never raises for missing folders — an agent that is not
    installed simply contributes nothing.
    """
    kinds = list(kinds or AGENT_DEFAULTS)
    sessions = []
    for kind in kinds:
        for base in AGENT_DEFAULTS.get(kind, []):
            if not base.exists():
                continue
            if kind == "opencode":
                if base.suffix == ".db":
                    sessions.append({"agent": kind, "path": str(base)})
                continue
            if kind == "cursor":
                files = sorted(
                    (p for p in base.glob("*/*/store.db")),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
            elif kind == "claude-code":
                files = [p for p in base.glob("**/*.jsonl")
                         if not p.name.startswith("skill-")]
            elif kind == "codex":
                files = sorted(
                    (p for p in base.glob("**/*.jsonl*") if p.suffix in (".jsonl", ".zst")),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
            elif kind == "gemini-cli":
                files = sorted(base.glob("*/chats/session-*.jsonl"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
            elif kind == "copilot-cli":
                files = sorted(base.glob("*/events.jsonl"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
            elif kind == "cline":
                files = []
                for messages_file in sorted(base.glob("**/sessions/*/*.messages.json")):
                    files.append(messages_file)
                for task_dir in sorted(base.glob("**/tasks/*")):
                    if (task_dir / "api_conversation_history.json").exists():
                        files.append(task_dir)
            else:
                files = list(base.glob("**/*.jsonl"))
            if kind == "claude-code":
                files = [p for p in files if not p.name.startswith("skill-")]
            for file_path in files[: max(0, limit)]:
                sessions.append({"agent": kind, "path": str(file_path)})
    return sessions


def codex_thread_catalog(db_path=None) -> dict:
    """Read the ChatGPT-desktop thread catalog (thread_id -> title, kind, cwd).

    Codex writes this for every thread the desktop app knows about;
    ``source_kind`` distinguishes local ``codex-cli`` threads from ``chatgpt``
    cloud conversations (whose transcripts stay server-side). Read-only; WAL
    sidecars are read as-is. Missing database means no metadata.
    """
    db_path = Path(db_path or Path.home() / ".codex" / "sqlite" / "codex-dev.db")
    if not db_path.exists():
        return {}
    catalog = {}
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT thread_id, display_title, source_kind, cwd FROM local_thread_catalog"
        )
        for thread_id, title, source_kind, cwd in rows:
            catalog[str(thread_id)] = {
                "title": title,
                "source_kind": source_kind,
                "cwd": cwd,
                "origin": "cloud (ChatGPT)" if "chatgpt" in str(source_kind) else "local",
            }
        connection.close()
    except sqlite3.Error:
        return {}
    return catalog


def collect_session_meta(sessions: list, catalog_db=None) -> dict:
    """Gather per-trace session metadata (title, origin, cwd) for imported sessions.

    Returns {trace_id: {"title", "origin", "cwd"}} — currently only Codex
    carries a real cloud/local distinction; every other agent's on-disk
    sessions are simply local. ``catalog_db`` overrides the Codex thread
    catalog location (used by tests; production reads ~/.codex).
    """
    meta = {}
    codex_catalog = None
    for entry in sessions:
        agent = entry.get("agent")
        if agent == "codex":
            if codex_catalog is None:
                codex_catalog = codex_thread_catalog(catalog_db)
            path = Path(entry["path"])
            # Rollout files embed their session id; read it cheaply.
            for record in _iter_jsonl(path):
                if isinstance(record, dict) and record.get("type") == "session_meta":
                    payload = record.get("payload") or {}
                    session_id = payload.get("session_id") or payload.get("id")
                    if session_id and str(session_id) in codex_catalog:
                        info = codex_catalog[str(session_id)]
                        meta[f"codex:{session_id}"] = {
                            "title": info["title"],
                            "origin": info["origin"],
                            "cwd": info["cwd"],
                        }
                    break
        else:
            source_path = Path(entry["path"])
            trace_hint = f"{agent}:"
            # Origin is local by construction: the transcript is a local file.
            meta.setdefault(trace_hint + source_path.name, {"origin": "local"})
    return meta


def import_agent_sessions(sessions: list) -> list:
    """Run the per-agent importers over discovered or explicitly given sessions."""
    raw_calls = []
    for entry in sessions:
        kind = entry["agent"]
        importer = IMPORTERS_BY_AGENT.get(kind)
        if importer is None:
            continue
        try:
            raw_calls.extend(importer(entry["path"]))
        except (OSError, sqlite3.Error) as error:
            # One unreadable session must not sink the import.
            print(f"switchit: skipping unreadable {kind} session {entry['path']}: {error}",
                  file=__import__("sys").stderr)
    return raw_calls
