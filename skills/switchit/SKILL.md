---
name: switchit
description: Plan and verify migration of an AI workflow from one model or service to another using traces, adapters, and replay checks. Use when a user wants to switch an AI provider or make an agent workflow portable.
---

# SwitchIt!

Help the user move an existing AI workflow to a different provider while preserving the behavior that matters to them. The skill can be invoked from any project with `$switchit`. It guides discovery and verification; it does not imply that every provider already has a working adapter.

1. Inspect the current application, its provider configuration, prompts, tool schemas, and authorized traces. Identify the requested target and the workflow's success criteria from context. Ask only for information that cannot be discovered and materially affects the migration.
2. Create a canonical map of sessions, model calls, tool calls, errors, latency, and usage. Preserve source IDs and distinguish provider-recorded tokens from estimates and missing usage. Keep credentials out of files and reports. Use redacted or synthetic examples when possible.
3. Group traces by user task and choose representative replay cases, including common paths, costly paths, tool use, and observed failures. Keep uncertain labels visible. Never treat a tool attempt as proof of task success.
4. Check the target provider's current capabilities and limits against the workflow: message format, tool calling, structured output, streaming, context size, caching, error handling, and data-handling constraints. Use official provider documentation when these details may have changed.
5. Build or adapt the provider connector within the user's authorized scope. Replay the same cases on source and target, then compare task outcome, tool behavior, latency, and usage using the same checks. Keep recorded, estimated, and unknown values separate.
6. Report what passes, what fails, what remains uncertain, and the concrete changes needed to switch. Do not claim migration readiness from category coverage or model confidence alone. Apply the switch only when the user has authorized that operational change.

## Using the SwitchIt! harness

If this project contains the SwitchIt! harness (a `switchit/` package), drive it with whichever surface your agent speaks:

- **MCP** (preferred for MCP-capable agents): register `switchit-mcp` (stdio) and call the tools — `switchit_import`, `switchit_map`, `switchit_cases`, `switchit_replay`, `switchit_report`, `switchit_run`, `switchit_status`, `switchit_read_report`, `switchit_list_providers`. Everything is offline until you pass `remote: true` with a live target.
- **CLI**: all stages are offline unless `--remote` is passed; keys come from the environment, never from arguments.

```sh
# Offline pipeline on the bundled synthetic data (no credentials needed):
python3 -m switchit.cli run --demo --state-dir /tmp/switchit-demo

# Real traces, step by step:
python3 -m switchit.cli import --jsonl traces.jsonl --app-config app_config.json --state-dir .switchit
python3 -m switchit.cli map --keep-edits --state-dir .switchit
python3 -m switchit.cli cases --state-dir .switchit
python3 -m switchit.cli replay --state-dir .switchit
python3 -m switchit.cli report --state-dir .switchit
```

Where the agent's judgment comes in:

- After `map`: review `workflows.json`. Correct names, merge or split clusters by editing `trace_ids`, set `"edited": true` on every workflow you touched, and re-run `map --keep-edits`. Flag low-confidence and `single_trace_cluster` workflows to the user instead of guessing.
- After `cases`: review `cases.json`. Replace defaults with explicit success checks where outcomes matter (`output_contains`, `tool_args_include`, `output_regex`, and the like). A historical model response alone is not proof of success; a judge model, if you add one by hand, is a fallible measurement tool, not ground truth.
- Before `replay --target <live>`: confirm the user authorizes sending redacted case content to that provider, then pass `--remote` and set the key in the environment.
- After `report`: walk the user through `prioritized_fixes` in order, and keep "uncertain" honest — unevaluable checks, mis-specified checks (they fail on the historical reference too), and un-checked tool drift are not passes.

The recorded adapter is the reference side of every comparison: it replays historical behavior, preserving its usage provenance, so the report can distinguish "the target changed" from "the check was wrong".

If no SwitchIt! harness is available in the project, implement only the smallest reproducible workflow needed for the user's actual system. Do not reuse assessment or third-party datasets outside their permitted scope.
