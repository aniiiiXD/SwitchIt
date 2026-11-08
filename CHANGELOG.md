# Changelog

All notable changes to SwitchIt! are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.2] - 2026-11-08

### Added
- Muse Code importer (Meta): event-sourced session.jsonl with runs, tool
  calls/results, recorded usage; subagent logs as their own traces.
- Grok Build importer (xAI): tolerant best-effort parser for
  ~/.grok/sessions (schema not publicly documented).
- Dot (OpenAI) documented: dot-created Codex threads are local rollouts,
  already covered by the Codex importer; dot's own context is cloud-side.

### Fixed
- Secret-field redaction regex assembled from a list, removing a
  credential-shaped literal that secret scanners flagged (GitGuardian
  false positive).

## [0.1.1] - 2026-11-08

Repository metadata release after the GitHub repo went public
(github.com/aniiiiXD/SwitchIt).

### Added
- Repository and issue-tracker URLs on PyPI and npm package metadata.

### Changed
- Version bump across PyPI, npm (harness), and npm (skill) to 0.1.1.

## [0.1.0] - 2026-11-06

First usable release: the complete offline pipeline plus explicit-opt-in live replay.

### Added

- Canonical trace store: sessions, traces, calls, with usage provenance kept
  separate (`recorded` / `estimated` / `unknown`) and per-field missing counts.
- Importers: JSON/JSONL, OpenTelemetry/OTLP JSON (gen_ai conventions),
  Langfuse-style exports, and Parquet / ZIP-of-`traces.parquet` (optional
  `pyarrow`).
- Session profiler (`profile` / `switchit_profile`): digs through the store
  and surfaces system prompts, a tool inventory with JSON schemas inferred
  from observed arguments, provider/model mix, error samples, and an
  editable inferred app configuration.
- Coding-agent session importers (`--agents`): read sessions from their local
  folders for Codex, Claude Code, OpenCode, Cursor, Cline, Gemini CLI, and
  GitHub Copilot CLI, plus Aider histories by explicit path — traces people
  already have, with zero instrumentation. Formats taken from public sources
  (each agent's own docs/source and the doubleagent research collection).
  Tolerant parsing, per-agent limits, same redaction and provenance rules as
  every other source. Windsurf has no public format yet and is not supported.
- Devin cloud importer (`--devin`, explicit opt-in): official v3 API session
  list as task-level traces (prompt, status, pull requests; ACUs kept out of
  token totals). Credentials from the environment only, mirroring the
  remote-replay safety model.
- MCP server (`switchit-mcp`, stdio, stdlib-only): ten tools covering the
  whole pipeline, so any MCP-capable agent can drive the harness natively.
  The remote opt-in guard carries through tool calls.
- Provider presets: `grok` (xAI), `groq`, `deepseek`, `mistral`, `openrouter`,
  `together`, `fireworks`, `ollama` (free local), `vllm` (local). Preset
  adapters label themselves correctly in errors and report their required
  environment variables.
- Secret redaction applied at import; redacted content is the only content
  stored, replayed, or reported.
- Provisional workflow grouping (TF-IDF cosine + tool-set overlap) with
  confidence and uncertainty signals, reviewer-edit preservation
  (`map --keep-edits`).
- Replay-case selection covering common, costly, risky, and failed workflows,
  with editable default success checks (10 check types; `unknown` never
  silently passes).
- Provider adapters: `recorded` baseline, `mock` (mirror/echo/canned/degrade/
  fail), and live `openai` (OpenAI-compatible incl. vLLM), `anthropic`,
  `bedrock` (Converse, optional `boto3`). Live adapters are inert without the
  explicit `--remote` flag; keys come from the environment only.
- Runner with per-case checkpointing and a run fingerprint that refuses to
  resume after cases, adapters, or runner code change.
- Chained replay (`replay --chain`): recorded tool fixtures extracted from
  history let the target's tool proposals continue to a final answer, so
  checks evaluate the end of the workflow offline. Unknown tools receive an
  explicit placeholder result and are flagged; no real business tool is ever
  executed.
- Comparison: pass / fail / error / uncertain verdicts with written reasons,
  tool behavior findings (missing / added / argument drift), a mis-specification
  guard for checks that also fail on the historical reference, failure groups,
  capability gaps, prioritized fixes, and usage totals by provenance.
- Local, self-contained JSON + HTML migration report (no server, no external
  assets).
- CLI: `import → map → cases → replay → report`, one-command `run`, and a
  bundled synthetic dataset via `--demo`.
- Companion `$switchit` agent skill and offline test suite (99 tests).
