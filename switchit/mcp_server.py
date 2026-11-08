"""MCP server: expose the SwitchIt! pipeline as tools for any MCP-capable agent.

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0)
using only the standard library, so any MCP client — Claude Code, Codex,
OpenCode, Cursor, or your own agent — can drive the harness natively:

    switchit_import → switchit_map → switchit_cases → switchit_replay
    → switchit_report → switchit_read_report

Safety model, unchanged from the CLI:

- Everything is local and offline unless the caller explicitly passes
  ``remote: true`` to ``switchit_replay`` with a live target; keys still come
  from the environment, never from tool arguments.
- Tool proposals are captured, never executed.
- Server logs go to stderr only; stdout carries protocol messages.

Register it in an MCP client with:

    { "command": "switchit-mcp", "args": [] }
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from types import SimpleNamespace

from . import __version__, providers as pv

PROTOCOL_VERSION = "2025-06-18"

TOOL_NAMES = (
    "switchit_import", "switchit_map", "switchit_cases", "switchit_replay",
    "switchit_report", "switchit_run", "switchit_profile", "switchit_status",
    "switchit_read_report", "switchit_list_providers",
)


def _schema(properties: dict, required: list | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _str(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _bool(desc: str) -> dict:
    return {"type": "boolean", "description": desc}


def _int(desc: str) -> dict:
    return {"type": "integer", "description": desc}


TOOLS = [
    {
        "name": "switchit_import",
        "description": (
            "Import authorized trace exports and optional app configuration into the "
            "canonical SwitchIt store (redacted, usage provenance preserved). Pass demo=true "
            "for the bundled synthetic dataset."
        ),
        "inputSchema": _schema({
            "state_dir": _str("Directory for store.json, workflows.json, cases.json, and the run."),
            "jsonl": {"type": "array", "items": {"type": "string"}, "description": "JSON/JSONL export paths."},
            "otlp": {"type": "array", "items": {"type": "string"}, "description": "OTLP JSON export paths."},
            "langfuse": {"type": "array", "items": {"type": "string"}, "description": "Langfuse export paths."},
            "parquet": {"type": "array", "items": {"type": "string"}, "description": "Parquet or ZIP-of-traces.parquet paths (needs pyarrow)."},
            "agents": _bool("Also import locally recorded coding-agent sessions (Codex, Claude Code, OpenCode, Cursor, Cline, Gemini CLI, Copilot CLI) from their default folders on this machine."),
            "aider": {"type": "array", "items": {"type": "string"}, "description": "Aider .aider.chat.history.md paths (project-local; explicit paths only)."},
            "devin": _bool("EXPLICIT opt-in: import Devin cloud sessions via the official API (needs DEVIN_API_KEY + DEVIN_ORG_ID in the environment). Task-level metadata only."),
            "app_config": _str("App configuration JSON path: prompts, tool schemas, provider options."),
            "demo": _bool("Import the bundled synthetic demo dataset instead of real inputs."),
        }),
    },
    {
        "name": "switchit_map",
        "description": (
            "Group imported traces into provisional workflows with evidence and uncertainty "
            "flags. Review the result and correct names/members by editing workflows.json "
            "(set edited=true), then call again with keep_edits=true."
        ),
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
            "threshold": _int("Similarity threshold 0..1 (default 0.34). Lower groups more aggressively."),
            "keep_edits": _bool("Preserve workflows previously marked edited."),
        }),
    },
    {
        "name": "switchit_cases",
        "description": "Select representative replay cases (common, costly, risky, failed) with default success checks. Edit cases.json to define real success before replaying.",
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
            "max_cases": _int("Maximum number of cases (default 24)."),
        }),
    },
    {
        "name": "switchit_replay",
        "description": (
            "Replay frozen cases through the recorded baseline and a target adapter, "
            "checkpointed per case. Fully offline with target=mock. Any live target requires "
            "remote=true AND the provider key already in the environment — this sends the "
            "redacted case requests to that provider."
        ),
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
            "target": {"type": "string", "enum": ["mock", "openai", "anthropic", "bedrock"] + sorted(pv.PROVIDER_PRESETS),
                       "description": "Target adapter or preset (grok, groq, deepseek, mistral, openrouter, together, fireworks, ollama, vllm)."},
            "remote": _bool("EXPLICIT opt-in to contact a live provider. Required for any non-mock target."),
            "target_model": _str("Model id for the target (required for vllm)."),
            "base_url": _str("Override the target endpoint."),
            "mock_mode": {"type": "string", "enum": list(pv.MockAdapter.MODES), "description": "Offline mock behavior (default mirror)."},
            "timeout": _int("HTTP timeout seconds for live targets."),
            "fresh": _bool("Ignore any previous checkpoint in the run dir."),
            "chain": _bool("Chained replay: continue tool proposals with recorded fixtures from history so checks evaluate the final answer offline. Never executes real business tools."),
        }),
    },
    {
        "name": "switchit_report",
        "description": "Compare completed replay results and write the JSON + HTML migration report (verdicts, failure groups, capability gaps, prioritized fixes, usage by provenance).",
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
        }),
    },
    {
        "name": "switchit_run",
        "description": "One-command pipeline: import → map → cases → replay → report. Use demo=true for the bundled synthetic dataset, or pass input paths.",
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
            "demo": _bool("Use the bundled synthetic demo dataset."),
            "jsonl": {"type": "array", "items": {"type": "string"}},
            "otlp": {"type": "array", "items": {"type": "string"}},
            "langfuse": {"type": "array", "items": {"type": "string"}},
            "parquet": {"type": "array", "items": {"type": "string"}},
            "agents": _bool("Also import locally recorded coding-agent sessions."),
            "app_config": _str("App configuration JSON path."),
            "target": {"type": "string", "enum": ["mock", "openai", "anthropic", "bedrock"] + sorted(pv.PROVIDER_PRESETS)},
            "remote": _bool("EXPLICIT opt-in to contact a live provider."),
            "target_model": _str("Model id for the target."),
            "mock_mode": {"type": "string", "enum": list(pv.MockAdapter.MODES)},
            "chain": _bool("Chained replay with recorded tool fixtures."),
        }),
    },
    {
        "name": "switchit_profile",
        "description": (
            "Dig through the imported sessions and surface everything inside: system prompts, "
            "a tool inventory with JSON schemas inferred from observed calls, provider/model mix, "
            "error samples, and usage by provenance. Writes profile.json and an editable "
            "app_config.inferred.json the user can review and reuse."
        ),
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
        }),
    },
    {
        "name": "switchit_status",
        "description": "Report which pipeline artifacts exist in the state directory (store, workflows, cases, run results, report) with headline counts.",
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
        }),
    },
    {
        "name": "switchit_read_report",
        "description": "Read the generated migration report (verdicts, prioritized fixes, failure groups, usage by provenance) so you can reason over it and advise the user.",
        "inputSchema": _schema({
            "state_dir": _str("SwitchIt state directory."),
        }),
    },
    {
        "name": "switchit_list_providers",
        "description": "List available target adapters and provider presets with their base URLs, default models, and required environment variables.",
        "inputSchema": _schema({}),
    },
]


def _defaults() -> dict:
    return {
        "state_dir": ".switchit",
        "threshold": 0.34,
        "keep_edits": False,
        "max_cases": 24,
        "baseline": "recorded",
        "target": "mock",
        "remote": False,
        "target_model": None,
        "base_url": None,
        "mock_mode": "mirror",
        "mock_text": None,
        "timeout": 60,
        "run_dir": None,
        "fresh": False,
        "chain": False,
        "agent_limit": 200,
        "aider": None,
        "devin": False,
        "jsonl": None,
        "otlp": None,
        "langfuse": None,
        "parquet": None,
        "app_config": None,
        "demo": False,
        "agents": False,
    }


def _namespace(arguments: dict) -> SimpleNamespace:
    values = _defaults()
    values.update({key: value for key, value in (arguments or {}).items() if value is not None})
    return SimpleNamespace(**values)


def _run_cli_tool(tool_name: str, arguments: dict) -> str:
    """Run a CLI subcommand with captured stdout; returns its output as text.

    The CLI prints to stdout, which on the MCP stdio transport is reserved for
    protocol messages — so stdout is captured and returned to the caller as the
    tool result, and anything unexpected goes to stderr.
    """
    from types import SimpleNamespace as NS  # noqa: F401  (kept for clarity)

    from . import cli

    args = _namespace(arguments)
    subcommand = tool_name.replace("switchit_", "", 1)
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            exit_code = {
                "import": cli.cmd_import,
                "map": cli.cmd_map,
                "cases": cli.cmd_cases,
                "replay": cli.cmd_replay,
                "report": cli.cmd_report,
                "run": cli.cmd_run,
            }[subcommand](args)
    except SystemExit as exit_exception:
        message = str(exit_exception)
        raise RuntimeError(message or f"{subcommand} refused to run") from exit_exception
    output = buffer.getvalue().strip()
    if exit_code not in (0, None):
        raise RuntimeError(f"{subcommand} exited with code {exit_code}\n{output}")
    return output


def _tool_status(arguments: dict) -> str:
    from pathlib import Path

    from . import core

    state_dir = Path((arguments or {}).get("state_dir") or ".switchit")
    lines = [f"state_dir: {state_dir}"]
    artifacts = {
        "store.json": "imported store",
        "workflows.json": "workflow map",
        "cases.json": "replay cases",
        "run/results.json": "replay results",
        "run/report.json": "migration report",
    }
    for relative, label in artifacts.items():
        path = state_dir / relative
        lines.append(f"{label}: {'present' if path.exists() else 'missing'} ({path})")
    store_path = state_dir / "store.json"
    if store_path.exists():
        store = core.load_store(store_path)
        totals = store["totals"]
        lines.append(f"traces: {totals['traces']}  calls: {totals['calls']}")
        for basis in ("recorded", "estimated", "unknown"):
            bucket = totals["usage"].get(basis) or {}
            lines.append(f"usage[{basis}]: calls={bucket.get('calls', 0)}")
    return "\n".join(lines)


def _tool_read_report(arguments: dict) -> str:
    import json as json_module
    from pathlib import Path

    state_dir = Path((arguments or {}).get("state_dir") or ".switchit")
    path = state_dir / "run" / "report.json"
    if not path.exists():
        raise RuntimeError(f"No report at {path}. Run switchit_report first.")
    report = json_module.loads(path.read_text(encoding="utf-8"))
    digest = {
        "verdicts": report.get("verdicts"),
        "capability_gaps": report.get("capability_gaps"),
        "failure_groups": report.get("failure_groups"),
        "prioritized_fixes": report.get("prioritized_fixes"),
        "usage": report.get("usage"),
        "boundaries": report.get("boundaries"),
        "case_count": len(report.get("cases") or []),
    }
    return json_module.dumps(digest, indent=1, ensure_ascii=False)


def _tool_list_providers(arguments: dict) -> str:
    lines = ["Core adapters: recorded (offline baseline), mock (offline target), openai, anthropic, bedrock"]
    lines.append("")
    lines.append("Provider presets (OpenAI-compatible):")
    for name, spec in pv.list_presets().items():
        model = spec["default_model"] or "(pass target_model)"
        keys = ", ".join(spec["key_envs"]) if spec["key_envs"] else "no key needed"
        lines.append(f"  {name:12s} {spec['base_url']:42s} model={model}  key: {keys}")
    lines.append("")
    lines.append("Meta Llama: use ollama/vllm (local), together/fireworks (hosted), or bedrock.")
    lines.append("Memory services (e.g. Supermemory) are business tools — captured as proposals, never replay targets.")
    return "\n".join(lines)


TOOL_HANDLERS = {
    "switchit_import": lambda arguments: _run_cli_tool("switchit_import", arguments),
    "switchit_map": lambda arguments: _run_cli_tool("switchit_map", arguments),
    "switchit_cases": lambda arguments: _run_cli_tool("switchit_cases", arguments),
    "switchit_profile": lambda arguments: _run_cli_tool("switchit_profile", arguments),
    "switchit_replay": lambda arguments: _run_cli_tool("switchit_replay", arguments),
    "switchit_report": lambda arguments: _run_cli_tool("switchit_report", arguments),
    "switchit_run": lambda arguments: _run_cli_tool("switchit_run", arguments),
    "switchit_status": _tool_status,
    "switchit_read_report": _tool_read_report,
    "switchit_list_providers": _tool_list_providers,
}


def handle_message(message: dict) -> dict | None:
    """Dispatch one decoded JSON-RPC message. Returns a response dict, or None
    for notifications and malformed requests."""
    if not isinstance(message, dict):
        return None
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "switchit", "version": __version__},
            },
        }
    if method == "notifications/initialized" or method.startswith("notifications/"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOL_HANDLERS:
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool {name!r}."}],
                    "isError": True,
                },
            }
        try:
            text = TOOL_HANDLERS[name](arguments)
            return {"jsonrpc": "2.0", "id": request_id,
                    "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except Exception as error:  # tool failures must reach the agent, not kill the server
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}],
                    "isError": True,
                },
            }
    if is_notification:
        return None
    return {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def handle_line(line: str) -> str | None:
    """Decode, dispatch, and encode one newline-delimited message."""
    line = line.strip()
    if not line:
        return None
    try:
        message = json.loads(line)
    except ValueError:
        print(f"switchit-mcp: ignoring non-JSON line: {line[:120]}", file=sys.stderr)
        return None
    response = handle_message(message)
    if response is None:
        return None
    return json.dumps(response, ensure_ascii=False)


def main() -> int:
    """Stdio loop. Stdout carries protocol messages; logs go to stderr."""
    print(f"switchit-mcp {__version__}: serving {len(TOOLS)} tools on stdio", file=sys.stderr)
    for line in sys.stdin:
        try:
            output = handle_line(line)
        except Exception as error:  # never let the server die mid-session
            request_id = None
            try:
                request_id = json.loads(line).get("id")
            except (ValueError, AttributeError):
                pass
            output = json.dumps({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32603, "message": f"Internal error: {error}"},
            })
            print(f"switchit-mcp: internal error: {error}", file=sys.stderr)
        if output is not None:
            sys.stdout.write(output + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
