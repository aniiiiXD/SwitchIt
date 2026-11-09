# SwitchIt!

**Codex, Claude Code, Devin, Grok, and Muse remain the agents SwitchIt helps you move between: it captures workflows from their sessions, maps dependencies, and tests whether another agent can reproduce the required outcomes; the dynamic harness would adapt tools, context, and execution settings to make that transition work, wherever each agent’s APIs allow.**

SwitchIt! helps teams move AI workflows from one provider to another with evidence that the important behavior survives the move.

The harness ingests authorized traces and application configuration, maps the workflows people actually use, selects representative replay cases, runs them against a target provider, and compares task outcomes, tool behavior, latency, and token usage. It produces a migration report with failures, uncertainties, and concrete changes needed.

The companion skill guides an agent through reviewing the evidence, resolving ambiguous workflows, and preparing a migration plan. The harness performs the reproducible imports, replays, comparisons, and checks. Use `$switchit` from any project to ask for a provider migration plan or a verified switch; see the [skill](skills/switchit/SKILL.md), the [system map](docs/system-map.svg), and the [migration run](docs/migration-run.svg).

First milestone — shipped in `switchit/`: JSON/JSONL, OTLP, Langfuse-style, and Parquet importers; one recorded baseline adapter; mock and live (OpenAI-compatible, Anthropic, Bedrock Converse) target adapters; and a complete offline replay loop with JSON + HTML reports on synthetic traces.

## Install

```sh
pip install switchit-harness        # from PyPI, once published
# or from this repository:
pip install .

switchit --version
python3 -m switchit.cli --version   # same thing without the entry point
```

The core is stdlib-only. Add extras only if you need them: `pip install 'switchit-harness[parquet]'` for Parquet imports, `'switchit-harness[bedrock]'` for the Bedrock adapter.

To install the companion `$switchit` agent skill for tools that read `~/.agents/skills`:

```sh
./scripts/install-skill.sh
```

## Quick start (fully offline, no credentials)

```sh
# One command on the bundled synthetic demo dataset:
python3 -m switchit.cli run --demo --state-dir /tmp/switchit-demo
open /tmp/switchit-demo/run/report.html

# Or step by step on the files in examples/:
python3 -m switchit.cli import --jsonl examples/traces.jsonl --app-config examples/app_config.json --state-dir .switchit
python3 -m switchit.cli map --state-dir .switchit          # provisional workflow map; edit workflows.json to correct it
python3 -m switchit.cli map --keep-edits --state-dir .switchit   # re-runs keep your edits
python3 -m switchit.cli cases --state-dir .switchit        # replay set; edit cases.json to add real success checks
python3 -m switchit.cli replay --state-dir .switchit       # recorded baseline vs mock target, checkpointed
python3 -m switchit.cli report --state-dir .switchit       # report.json + report.html
```

## What each stage produces

| Stage | Artifact | Notes |
|---|---|---|
| `import` | `<state-dir>/store.json` | Canonical sessions, traces, calls. Secrets redacted at import — redacted content is the only content stored, replayed, or reported. Usage provenance kept separate: `recorded` / `estimated` / `unknown`. Sources: your exports **and** coding-agent session folders you already have (`--agents` reads Codex, Claude Code, and OpenCode locally). |
| `profile` | `profile.json` + `app_config.inferred.json` | Digs through the sessions: system prompts, tool inventory with schemas inferred from observed calls, provider/model mix, error samples. The inferred app config is a ready-to-edit starting point. |
| `map` | `workflows.json` | TF-IDF + tool-overlap grouping with confidence and uncertainty flags. Labels are triage aids; set `"edited": true` and fix names/members to correct them. |
| `cases` | `cases.json` | One case per selected trace covering common, costly, risky, and failed workflows, with default checks. Edit to add explicit success checks — a historical model response alone is not proof of success. |
| `replay` | `run/results.json` + `run/run_state.json` | Same cases through baseline and target adapters. Tool proposals are captured, never executed. Checkpointed after every case; a fingerprint refuses to resume after cases, adapters, or runner code changed. |
| `report` | `run/report.json` + `run/report.html` | Verdicts (pass / fail / error / uncertain), failure groups, capability gaps, prioritized fixes, usage by provenance. Local, self-contained HTML. |

## Connectors

- **Inputs:** OpenTelemetry/OTLP JSON (`--otlp`), JSON/JSONL (`--jsonl`), Langfuse-style exports (`--langfuse`), and Parquet / ZIP-of-`traces.parquet` (`--parquet`, needs the optional `pyarrow` package). A simple app-configuration JSON (`--app-config`) supplies prompts and tool definitions.
- **Providers:** recorded baseline and mock adapters for offline work; OpenAI-compatible HTTP, Anthropic Messages, and Amazon Bedrock Converse for live tests. Provider presets fill in the details: `--target grok` (xAI), `groq`, `deepseek`, `mistral`, `openrouter`, `together`, `fireworks`, `ollama` (free, fully local), `vllm` (local). Meta Llama runs through `ollama`/`vllm`/`together`/`fireworks`/`bedrock`.
- **Secrets:** environment variables only — `OPENAI_API_KEY`, `XAI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, the standard AWS chain, and so on per provider. Keys are never accepted on the command line or written to files. Local import and analysis do not contact a provider.
- **Memory services:** products like Supermemory are business tools your agent calls, not chat-completion providers — so they are captured as tool proposals during replay, never executed, and are not replay targets.

## Live replay is an explicit opt-in

```sh
export OPENAI_API_KEY='...'
python3 -m switchit.cli replay --target openai --target-model gpt-4o-mini --remote --state-dir .switchit
```

Without `--remote`, live targets refuse to run. With it, the redacted case requests are sent to the target provider and billed accordingly; usage is recorded from provider responses, and requests that fail before a response keep their consumption counted separately as unknown.

## Traces you already have: coding agents

Most developers already have rich agent traces on disk without ever instrumenting anything. SwitchIt reads them directly, with the same redaction and provenance rules as every other source:

```sh
switchit import --agents                        # Codex, Claude Code, OpenCode session folders
switchit profile --state-dir .switchit          # prompts, tool schemas, provider mix — dug out automatically
```

- **Codex**: `~/.codex/sessions/**/*.jsonl` — model calls, `function_call`s and outputs, token counts
- **Claude Code**: `~/.claude/projects/**/*.jsonl` — transcripts with `tool_use` blocks and per-message usage
- **OpenCode**: `~/.local/share/opencode/opencode.db` — message/part tables (read-only SQLite)
- **Muse Code** (Meta): `~/.local/share/muse/sessions/**/session.jsonl` — event-sourced logs: runs, tool calls + results, recorded usage; subagent logs import as their own traces
- **Grok Build** (xAI): `~/.grok/sessions/<cwd>/<id>/` — schema-accurate parser built from the open-source xai-org/grok-build storage module: `chat_history.jsonl` ConversationItems (user_query stripped, tool_calls, tool_results), `summary.json`, `system_prompt.txt`, session-level usage from `signals.json` (estimator-labeled), stop reasons and failed-tool marks backfilled from `updates.jsonl`
- **Dot** (OpenAI): dot's own context is cloud-side, but **dot-created Codex threads on your Mac are local rollouts** — already covered by the Codex importer; no separate importer needed
- **Cursor**: `~/.cursor/chats/*/*/store.db` — chat messages per workspace, system prompts, agentic tool calls (Cursor reports no usage, so those calls land in the `unknown` bucket)
- **Cline**: `~/.cline/data/sessions/` and VS Code `globalStorage/saoudrizwan.claude-dev/tasks/` — SDK message files with per-message metrics, legacy task conversations with usage from `ui_messages.json`
- **Gemini CLI**: `~/.gemini/tmp/*/chats/session-*.jsonl` — records with `toolCalls` (args, results, status) and per-message token counts; `$set`/`$patch`/`$rewindTo` update records applied by id
- **Copilot CLI** (incl. Desktop-bundled sessions): `~/.copilot/session-state/*/events.jsonl` — the event log: prompts, `assistant.message` with `toolRequests`, tool results, `session.usage_record` usage, system prompt
- **Aider**: explicit paths only (`--aider .aider.chat.history.md`) — histories live per-project. Task-level evidence: prompts, assistant text, tool output as blockquotes; no structured tool calls or usage, and traces are marked accordingly
- **Devin** (cloud): explicit opt-in (`--devin`, needs `DEVIN_API_KEY` + `DEVIN_ORG_ID` in the environment) via the official v3 API — task-level metadata per session (prompt, status, pull requests, ACU consumption; ACUs are compute units and stay out of token totals). Message-level transcripts are not exposed by the API, so these traces support workflow discovery, not replay
- **Windsurf**: not supported — no publicly documented chat-store format (it is mid-migration to Devin per community research); will follow once a format is public

Everything downstream — workflow map, replay cases with tool fixtures, migration report — works the same on these as on any other source. `--agent-limit` caps how many session files are read (newest first, default 200).

## If your workflow already uses business tools (Supermemory, internal APIs, …)

Their calls are treated as first-class evidence at every stage, and a chained replay can complete the workflow offline:

1. **Import** — the tool calls are already in your traces; import captures them, and the workflow map shows which workflows depend on which tools.
2. **Cases** — each case carries `tool_fixtures`: what the tool actually returned in history, extracted automatically. Edit them in `cases.json` if needed.
3. **Replay with `--chain`** — when the target proposes a tool call, the recorded result is fed back and the model continues, so success checks evaluate the workflow's **final answer**, not just the first proposal. If the target invents a tool history never used, it gets an explicit placeholder result and the gap is reported.
4. **The line that never moves** — the harness never executes a real business tool. No reads against your live Supermemory, no writes to your data. Live read-only fixtures against an authorized environment are a possible future opt-in, separate from the default offline path.

The same mechanism covers every business tool, not just memory services — Supermemory, Stripe, internal CRUD APIs — because the fixture layer is tool-agnostic by design.

## Any agent can drive it (MCP)

The harness ships a built-in MCP server, so any MCP-capable agent — Claude Code, Codex, OpenCode, Cursor, or your own — can run the whole pipeline as native tools instead of shelling out:

```sh
switchit-mcp        # stdio MCP server; register it in your client as:
```

```json
{ "switchit": { "command": "switchit-mcp", "args": [] } }
```

Exposed tools: `switchit_import`, `switchit_profile`, `switchit_map`, `switchit_cases`, `switchit_replay`, `switchit_report`, `switchit_run`, `switchit_status`, `switchit_read_report`, `switchit_list_providers`. The safety model carries over unchanged: everything is offline until the agent explicitly passes `remote: true` with a live target, and keys still come from the environment — an agent cannot talk a key out of you through a tool argument. The `$switchit` skill and the CLI remain equivalent entry points; use whichever your agent speaks.

## Tests

```sh
python3 -m unittest discover -s tests -p "test_*.py" -t .
```

All tests are offline and use only synthetic data: connector shapes, redaction, usage reconciliation ("every call counted exactly once"), grouping behavior, verdict logic, checkpoint/resume, fingerprint refusal, report contents, and the no-secrets guarantee.

## Publishing

```sh
python3 -m pip install build twine
python3 -m build                       # sdist + wheel into dist/
python3 -m twine check dist/*          # metadata + README rendering
python3 -m twine upload dist/*         # to PyPI (configure credentials first)
```

Cut a release by bumping `version` in `pyproject.toml` and adding a `CHANGELOG.md` entry.

## Code layout

```text
switchit/
  core.py        import, normalize, redact, workflow map, replay selection
  providers.py   provider adapters and capability descriptions
  runner.py      replay, compare, report, checkpoints
  cli.py         import → map → cases → replay → report; one-command run
  demo_data.py   bundled synthetic dataset for --demo
tests/           offline end-to-end and connector-shape checks
examples/        synthetic traces and offline provider config
skills/switchit/ SKILL.md: guide the agent through evidence and migration decisions
```

## Boundaries

Workflow labels and similarity scores are triage aids, not measured accuracy. For a workflow that needs a real tool to complete, a single-call replay can verify the proposed tool request but must leave the end-to-end outcome unverified until a safe tool fixture or authorized test environment exists. The Hyde assessment dataset and results are not inputs to this project.
