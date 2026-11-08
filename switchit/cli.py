"""Command line interface: import → map → cases → replay → report.

Subcommands can be run one at a time (so a human or agent can review and edit
the intermediate artifacts) or as a single command via ``run``.

Local work (import, map, cases, offline replay with recorded/mock adapters)
never contacts a provider. Live replay requires BOTH the explicit ``--remote``
flag and a key in the environment; there is no way to pass credentials on the
command line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, agent_sessions, core, demo_data, devin, providers as pv, runner

DEFAULT_STATE_DIR = ".switchit"

OFFLINE_TARGETS = ("mock",)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _state_dir(args) -> Path:
    return Path(getattr(args, "state_dir", DEFAULT_STATE_DIR))


def _store_path(args) -> Path:
    return _state_dir(args) / "store.json"


def _workflows_path(args) -> Path:
    return _state_dir(args) / "workflows.json"


def _cases_path(args) -> Path:
    return _state_dir(args) / "cases.json"


def _run_dir(args) -> Path:
    run_dir = getattr(args, "run_dir", None)
    return Path(run_dir) if run_dir else _state_dir(args) / "run"


def _load_store_or_exit(args) -> dict:
    path = _store_path(args)
    if not path.exists():
        sys.exit(f"No store at {path}. Run 'import' first.")
    return core.load_store(path)


def _load_workflows_or_exit(args) -> dict:
    path = _workflows_path(args)
    if not path.exists():
        sys.exit(f"No workflow map at {path}. Run 'map' first.")
    return core.load_workflows(path)


def _load_cases_or_exit(args) -> dict:
    path = _cases_path(args)
    if not path.exists():
        sys.exit(f"No cases at {path}. Run 'cases' first.")
    return core.load_cases(path)


def _print_usage_totals(store: dict) -> None:
    totals = store["totals"]["usage"]
    print(f"  traces: {store['totals']['traces']}  calls: {store['totals']['calls']}")
    for basis in ("recorded", "estimated", "unknown"):
        bucket = totals.get(basis) or {}
        print(
            f"  usage[{basis}]: calls={bucket.get('calls', 0)}"
            f" input={bucket.get('input_tokens_known', 0):,}"
            f" output={bucket.get('output_tokens_known', 0):,}"
            f" missing_any={bucket.get('calls_missing_either', 0)}"
        )


def _collect_import_inputs(args) -> dict:
    inputs = {}
    for kind, flag in (
        ("jsonl", "jsonl"),
        ("otlp", "otlp"),
        ("langfuse", "langfuse"),
        ("parquet", "parquet"),
    ):
        paths = getattr(args, flag, None) or []
        if paths:
            inputs[kind] = list(paths)
    return inputs


def _target_config(args) -> dict:
    target = args.target
    if target in pv.PROVIDER_PRESETS:
        config = pv.resolve_preset(target, {"base_url": args.base_url, "model": args.target_model})
        return {
            "model": config["model"],
            "base_url": args.base_url or config["base_url"],
            "timeout": args.timeout,
            "api_key": pv.read_preset_key(target),
            "preset": target,
        }
    return {
        "model": args.target_model,
        "mock_mode": args.mock_mode,
        "mock_text": args.mock_text,
        "base_url": args.base_url,
        "timeout": args.timeout,
        "api_key": _live_key(args.target),
    }


def _live_key(target: str):
    """Read the live adapter key from the environment. Never from arguments."""
    if target in pv.PROVIDER_PRESETS:
        return pv.read_preset_key(target)
    if target == "openai":
        return os.environ.get("SWITCHIT_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if target == "anthropic":
        return os.environ.get("SWITCHIT_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    return None


TARGET_CHOICES = ["mock", "openai", "anthropic", "bedrock"] + sorted(pv.PROVIDER_PRESETS)


def _build_adapters(args):
    remote = bool(args.remote)
    target = args.target
    if target != "mock" and not remote:
        sys.exit(
            f"Refusing to contact the live '{target}' provider. Remote replay is an explicit "
            "opt-in: re-run with --remote (and the provider's key in the environment) to "
            "enable data transfer. Offline target: --target mock."
        )
    target_config = _target_config(args)
    target_adapter = pv.build_adapter(target, target_config, remote=remote)
    baseline_adapter = pv.build_adapter(args.baseline, {}, remote=False)
    baseline_spec = pv.adapter_spec(args.baseline, {})
    target_spec = pv.adapter_spec(target, target_config)
    if getattr(args, "chain", False):
        target_spec["chain"] = True
    return baseline_adapter, target_adapter, baseline_spec, target_spec


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_import(args) -> int:
    inputs = _collect_import_inputs(args)
    demo = bool(getattr(args, "demo", False))
    agents = getattr(args, "agents", False)
    if not inputs and not demo and not agents and not getattr(args, "devin", False) and not getattr(args, "aider", None):
        sys.exit("Nothing to import. Pass one of --jsonl, --otlp, --langfuse, --parquet, --agents, --devin, --aider (or use 'run --demo').")
    calls = []
    source_files = []
    notes = []
    session_meta = None
    if demo:
        demo_calls = [
            core.make_call(raw, "demo", "demo_data.py")
            for raw in core.parse_jsonl_lines(demo_data.demo_lines(), "demo_data.py")
        ]
        calls.extend(demo_calls)
        source_files.append({"importer": "demo", "path": "demo_data.py", "records": len(demo_calls)})
        notes.append("Includes the bundled synthetic demo dataset (demo_data.py).")
    if agents:
        discovered = agent_sessions.discover_agent_sessions(limit=args.agent_limit)
        if discovered:
            by_agent = {}
            for entry in discovered:
                by_agent[entry["agent"]] = by_agent.get(entry["agent"], 0) + 1
            summary = ", ".join(f"{agent}: {count}" for agent, count in sorted(by_agent.items()))
            print(f"Discovered coding-agent sessions ({summary}); importing…")
            agent_calls = agent_sessions.import_agent_sessions(discovered)
            calls.extend(
                core.make_call(raw, "agents", "local-session-folders") for raw in agent_calls
            )
            session_meta = agent_sessions.collect_session_meta(discovered)
            source_files.append({"importer": "agents", "path": "auto-discovered local session folders",
                                 "records": len(agent_calls)})
            notes.append(
                "Includes locally recorded coding-agent sessions (Codex, Claude Code, OpenCode); "
                "same redaction and provenance rules as every other source."
            )
        else:
            print("No coding-agent session folders found.")
    for aider_path in getattr(args, "aider", None) or []:
        aider_calls = agent_sessions.import_aider(aider_path)
        calls.extend(core.make_call(raw, "agents", str(aider_path)) for raw in aider_calls)
        source_files.append({"importer": "agents", "path": str(aider_path), "records": len(aider_calls)})
        if aider_calls:
            notes.append(
                "Aider histories are task-level evidence (no model-level calls or usage); "
                "their traces carry assistant/task text and tool output but no replayable calls."
            )
    if getattr(args, "devin", False):
        print("Devin import opted in: querying the official Devin API (api.devin.ai)…")
        devin_calls = devin.import_devin_remote(limit=getattr(args, "agent_limit", 50))
        calls.extend(core.make_call(raw, "devin", "api.devin.ai") for raw in devin_calls)
        source_files.append({"importer": "devin", "path": "api.devin.ai/v3 sessions", "records": len(devin_calls)})
        notes.append(
            "Devin cloud sessions are task-level metadata from the official API "
            "(prompt, status, pull requests); message-level transcripts are not exposed, "
            "and these traces support workflow discovery, not replay."
        )
        session_meta = session_meta or {}
        for raw in devin_calls:
            if raw.get("origin"):
                session_meta[raw["trace_id"]] = {"origin": raw["origin"]}
    for kind, paths in inputs.items():
        importer = core.IMPORTERS[kind]
        for path in paths:
            raw_calls = importer(path)
            for raw in raw_calls:
                calls.append(core.make_call(raw, kind, str(path)))
            source_files.append({"importer": kind, "path": str(path), "records": len(raw_calls)})
    app_config = None
    if args.app_config:
        with open(args.app_config, "r", encoding="utf-8") as stream:
            app_config = json.load(stream)
    elif demo:
        app_config = demo_data.DEMO_APP_CONFIG
    calls.sort(key=lambda call: (call["trace_id"], call["call_index"], call["call_id"]))
    all_notes = [
        "All message content was redacted at import; redacted content is the only content stored, replayed, or reported.",
    ] + notes
    if app_config and isinstance(app_config, dict):
        all_notes.append(
            "App configuration supplies prompts and tool definitions; it is descriptive, not evidence of behavior."
        )
    store = core.build_store(calls, app_config=app_config, source_files=source_files,
                             notes=all_notes, session_meta=session_meta if agents else None)
    core.save_store(store, _store_path(args))
    print(f"Imported to {_store_path(args)}")
    _print_usage_totals(store)
    return 0


def cmd_map(args) -> int:
    store = _load_store_or_exit(args)
    previous = None
    if getattr(args, "keep_edits", False) and _workflows_path(args).exists():
        previous = core.load_workflows(_workflows_path(args))
    workflow_map = core.group_workflows(store, threshold=getattr(args, "threshold", 0.34))
    if previous:
        workflow_map = core.apply_edits(workflow_map, previous)
    core.save_workflows(workflow_map, _workflows_path(args))
    print(f"Workflow map written to {_workflows_path(args)}")
    for workflow in workflow_map["workflows"]:
        flags = ",".join(workflow["uncertainty"]) or "-"
        print(
            f"  {workflow['id']}  conf={workflow['confidence']:8s}  traces={workflow['stats']['traces']:3d}"
            f"  {workflow['name']}  [{flags}]"
        )
    if workflow_map["unassigned_trace_ids"]:
        print(f"  unassigned: {len(workflow_map['unassigned_trace_ids'])} trace(s)")
    print("  Labels are triage aids. Edit workflows.json (set \"edited\": true) to correct them.")
    return 0


def cmd_cases(args) -> int:
    store = _load_store_or_exit(args)
    workflow_map = _load_workflows_or_exit(args)
    cases_doc = core.select_cases(store, workflow_map, max_cases=getattr(args, "max_cases", 24))
    core.save_cases(cases_doc, _cases_path(args))
    print(f"Replay set written to {_cases_path(args)} ({len(cases_doc['cases'])} cases)")
    for case in cases_doc["cases"]:
        origins = ",".join(case["origins"])
        tools = ",".join(sorted({call["name"] for call in case["historical"]["tool_calls"] if call["name"]})) or "-"
        print(f"  {case['case_id']}  {case['workflow_id']:12s} [{origins:14s}] tools: {tools}")
    print("  Default checks are starting points. Edit cases.json to define real success checks.")
    return 0


def cmd_profile(args) -> int:
    """Dig through the store: system prompts, tool inventory with inferred
    schemas, provider mix, errors. Writes profile.json and an editable
    app_config.inferred.json."""
    store = _load_store_or_exit(args)
    document = core.build_profile(store)
    profile_path = _state_dir(args) / "profile.json"
    inferred_path = _state_dir(args) / "app_config.inferred.json"
    core.atomic_write_json(profile_path, document["profile"])
    core.atomic_write_json(inferred_path, document["app_config_inferred"])
    profile = document["profile"]
    totals = profile["totals"]
    print(f"Profile written to {profile_path}")
    print(f"Inferred app config written to {inferred_path} (edit before reuse)")
    print(f"  traces: {totals['traces']}  calls: {totals['calls']}  sessions: {totals['sessions']}  errored: {totals['calls_with_errors']}")
    for prompt in profile["system_prompts"][:3]:
        print(f"  system prompt (x{prompt['calls_using']}): {prompt['excerpt'][:80]}…")
    for tool in profile["tools"][:8]:
        schema = tool["schema_inferred"]
        keys = ", ".join(schema.get("properties", {})) or "-"
        print(f"  tool {tool['name']}: {tool['proposals']} proposal(s) in {tool['traces']} trace(s); args: {keys}")
    for provider in profile["providers"][:3]:
        print(f"  provider {provider['provider']}/{provider['model']}: {provider['calls']} call(s)")
    if profile.get("session_origins"):
        origins = ", ".join(f"{origin}: {count}" for origin, count in sorted(profile["session_origins"].items()))
        print(f"  session origins: {origins}")
    for session in profile.get("named_sessions", [])[:4]:
        print(f"  session '{session['title']}' [{session.get('origin', 'local')}]: {session['calls']} call(s)")
    return 0


def cmd_replay(args) -> int:
    cases_doc = _load_cases_or_exit(args)
    if not cases_doc.get("cases"):
        sys.exit("cases.json contains no cases; nothing to replay.")
    baseline_adapter, target_adapter, baseline_spec, target_spec = _build_adapters(args)
    results_doc = runner.run_replay(
        cases_doc,
        baseline_adapter,
        target_adapter,
        baseline_spec,
        target_spec,
        _run_dir(args),
        resume=not args.fresh,
    )
    verdicts = {}
    for result in results_doc["case_results"]:
        verdicts[result["verdict"]] = verdicts.get(result["verdict"], 0) + 1
    print(f"Replay complete; results in {_run_dir(args) / 'results.json'}")
    print(f"  verdicts: {json.dumps(verdicts, sort_keys=True)}")
    return 0


def cmd_report(args) -> int:
    run_dir = _run_dir(args)
    results_path = run_dir / "results.json"
    if not results_path.exists():
        sys.exit(f"No replay results at {results_path}. Run 'replay' first.")
    with open(results_path, "r", encoding="utf-8") as stream:
        results_doc = json.load(stream)
    cases_doc = _load_cases_or_exit(args)
    baseline_kind = (results_doc.get("baseline_spec") or {}).get("kind", "recorded")
    target_kind = (results_doc.get("target_spec") or {}).get("kind", "mock")
    capabilities_baseline = pv.build_adapter(baseline_kind, {}, remote=False).capabilities()
    capabilities_target = pv.build_adapter(target_kind, (results_doc.get("target_spec") or {}), remote=False).capabilities()
    summary = runner.aggregate(results_doc, cases_doc, capabilities_baseline, capabilities_target)
    report = runner.build_report(
        results_doc,
        summary,
        cases_doc,
        meta={
            "cases_by_id": {case["case_id"]: case for case in cases_doc.get("cases") or []},
            "capabilities": {"baseline": capabilities_baseline, "target": capabilities_target},
        },
    )
    paths = runner.write_report(report, run_dir)
    verdicts = summary["verdicts"]
    print(f"Report written to {paths['html']} and {paths['json']}")
    print(
        f"  pass={verdicts.get('pass', 0)} fail={verdicts.get('fail', 0)}"
        f" error={verdicts.get('error', 0)} uncertain={verdicts.get('uncertain', 0)}"
    )
    for fix in summary["prioritized_fixes"][:5]:
        print(f"  P{fix['priority']} {fix['title']}")
    return 0


def cmd_run(args) -> int:
    """One-command pipeline: import → map → cases → replay → report."""
    if not args.demo and not _collect_import_inputs(args):
        sys.exit("Provide inputs (--jsonl/--otlp/--langfuse/--parquet) or pass --demo.")
    cmd_import(args)
    cmd_map(args)
    cmd_cases(args)
    cmd_replay(args)
    cmd_report(args)
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m switchit.cli",
        description="SwitchIt! — evidence-based AI provider migration harness.",
    )
    parser.add_argument("--version", action="version", version=f"switchit {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_state_args(sub):
        sub.add_argument("--state-dir", default=DEFAULT_STATE_DIR,
                         help="Directory for store.json, workflows.json, cases.json, and the run (default: .%(default)s)")

    common_import = argparse.ArgumentParser(add_help=False)
    common_import.add_argument("--jsonl", nargs="*", help="JSON/JSONL call exports to import")
    common_import.add_argument("--otlp", nargs="*", help="OpenTelemetry/OTLP JSON exports to import")
    common_import.add_argument("--langfuse", nargs="*", help="Langfuse-style JSON exports to import")
    common_import.add_argument("--parquet", nargs="*", help="Parquet (or ZIP of traces.parquet) exports; needs pyarrow")
    common_import.add_argument("--app-config", help="App configuration JSON: prompts, tool definitions, provider options")
    common_import.add_argument("--agents", action="store_true",
                               help="Also import locally recorded coding-agent sessions (Codex, Claude Code, OpenCode, Cursor, Cline, Gemini CLI, Copilot CLI) from their default folders")
    common_import.add_argument("--agent-limit", type=int, default=200,
                               help="Maximum agent session files per agent (newest first, default: %(default)s)")
    common_import.add_argument("--aider", nargs="*", metavar="HISTORY_MD",
                               help="Aider chat-history files (.aider.chat.history.md) — project-local, so paths must be explicit")
    common_import.add_argument("--devin", action="store_true",
                               help="EXPLICIT opt-in: import Devin cloud sessions via the official API. "
                                    "Requires DEVIN_API_KEY and DEVIN_ORG_ID in the environment; task-level metadata only")

    sub = subparsers.add_parser("import", parents=[common_import], help="Import and normalize traces into the canonical store")
    add_state_args(sub)
    sub.set_defaults(func=cmd_import)

    sub = subparsers.add_parser("map", help="Provisionally group traces into workflows")
    add_state_args(sub)
    sub.add_argument("--threshold", type=float, default=0.34, help="Similarity threshold (default: %(default)s)")
    sub.add_argument("--keep-edits", action="store_true",
                     help="Preserve workflows you marked edited in the previous map")
    sub.set_defaults(func=cmd_map)

    sub = subparsers.add_parser("cases", help="Select representative replay cases with default checks")
    add_state_args(sub)
    sub.add_argument("--max-cases", type=int, default=24, help="Maximum number of cases (default: %(default)s)")
    sub.set_defaults(func=cmd_cases)

    sub = subparsers.add_parser("profile", help="Dig through the sessions: prompts, tool inventory with inferred schemas, provider mix, errors")
    add_state_args(sub)
    sub.set_defaults(func=cmd_profile)

    common_replay = argparse.ArgumentParser(add_help=False)
    common_replay.add_argument("--baseline", default="recorded", choices=["recorded"],
                               help="Baseline adapter for reference behavior (default: recorded history)")
    common_replay.add_argument("--target", default="mock", choices=TARGET_CHOICES,
                               help="Target adapter: mock (offline), openai, anthropic, bedrock, or a preset "
                                    "(grok, groq, deepseek, mistral, openrouter, together, fireworks, ollama, vllm)")
    common_replay.add_argument("--target-model", default=None, help="Model id for the target adapter")
    common_replay.add_argument("--base-url", default=None, help="Override the target endpoint (e.g. a local vLLM URL)")
    common_replay.add_argument("--mock-mode", default="mirror", choices=pv.MockAdapter.MODES,
                               help="Behavior of the mock adapter (default: mirror)")
    common_replay.add_argument("--mock-text", default=None, help="Canned text for mock canned/degrade modes")
    common_replay.add_argument("--timeout", type=int, default=pv.DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout seconds")
    common_replay.add_argument("--remote", action="store_true",
                               help="EXPLICIT opt-in to send redacted case content to a live provider. "
                                    "Required for any live target; keys come from the environment.")
    common_replay.add_argument("--chain", action="store_true",
                               help="Chained replay: continue the target's tool proposals with recorded tool "
                                    "fixtures from history, so checks evaluate the workflow's final answer "
                                    "offline. No real business tool is ever executed.")
    common_replay.add_argument("--run-dir", default=None, help="Run directory (default: <state-dir>/run)")
    common_replay.add_argument("--fresh", action="store_true", help="Ignore any previous checkpoint in the run dir")

    sub = subparsers.add_parser("replay", parents=[common_replay], help="Replay cases through baseline and target adapters")
    add_state_args(sub)
    sub.set_defaults(func=cmd_replay)

    sub = subparsers.add_parser("report", help="Compare results and write the JSON + HTML migration report")
    add_state_args(sub)
    sub.add_argument("--run-dir", default=None, help="Run directory (default: <state-dir>/run)")
    sub.set_defaults(func=cmd_report)

    sub = subparsers.add_parser("run", parents=[common_import, common_replay],
                                help="One-command pipeline: import → map → cases → replay → report")
    add_state_args(sub)
    sub.add_argument("--demo", action="store_true", help="Use the bundled synthetic demo dataset")
    sub.set_defaults(func=cmd_run)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ImportError as error:  # e.g. parquet without pyarrow
        sys.exit(str(error))
    except pv.ProviderError as error:
        sys.exit(f"provider error: {error}")
    except RuntimeError as error:
        sys.exit(str(error))
    except ValueError as error:
        sys.exit(f"input error: {error}")


if __name__ == "__main__":
    sys.exit(main())
