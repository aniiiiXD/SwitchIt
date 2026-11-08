# SwitchIt! harness (JavaScript/TypeScript)

The Node.js port of the SwitchIt! pipeline: import → profile → map → cases →
replay → report. Zero runtime dependencies; Node 20+.

## Supported in v0.1 (JS)

- **Import**: JSON/JSONL exports, Claude Code transcripts, Codex rollouts
  (plain; `.zst` requires Python-side import for now)
- **Canonical store** with secret redaction at import and token provenance
  separation (recorded / estimated / unknown)
- **Profile**: system prompts, tool inventory with schemas inferred from
  observed calls, provider mix, error samples
- **Workflow map**: TF-IDF cosine + tool-set overlap, uncertainty flags
- **Replay cases** with default checks, **recorded tool fixtures**, chained
  replay (`--chain`)
- **Adapters**: recorded baseline, mock (mirror/echo/canned/degrade/fail),
  live OpenAI-compatible (with grok/groq/deepseek/mistral/openrouter/together/
  fireworks/ollama/vllm presets) and Anthropic — explicit `--remote` opt-in,
  keys from the environment
- **Report**: JSON + self-contained HTML, four verdicts, prioritized fixes
- **MCP server**: `switchit-mcp`, six tools over stdio JSON-RPC

## Parity notes vs the Python harness (pip `switchit-harness`)

Not yet in JS v0.1: OTLP/Langfuse/Parquet importers, Devin cloud importer,
OpenCode/SQLite readers, workflow-edit preservation (`--keep-edits`), Bedrock
adapter. These land in 0.2. For the full feature set today, use the Python
package — the canonical store schema is identical.

## Run

```sh
switchit run --demo        # offline, no credentials
switchit import --agents   # Claude Code + Codex local sessions
switchit-mcp               # MCP server for any capable agent
```

Live providers:

```sh
export OPENAI_API_KEY=...  # or ANTHROPIC_API_KEY, XAI_API_KEY, ...
switchit replay --target openai --remote
```

## Build

```sh
npm install && npm run build
```

Note: the repository path contains `SwitchIt!` — webpack-reserved `!` breaks
some toolchains; the build uses plain `tsc`, which is unaffected.
