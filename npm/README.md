# switchit-skill

The **$switchit** agent skill: plan and verify migration of an AI workflow from
one model or provider to another using traces, adapters, and replay checks.

One command installs the skill for any agent that reads `~/.agents/skills`
(Claude Code, Codex, OpenCode, Cursor, and others):

```sh
npx switchit-skill
```

## What the skill does

When invoked with `$switchit` from any project, your agent walks a
six-step migration discipline:

1. Inspect the current app, its provider config, prompts, tool schemas, and
   authorized traces.
2. Build a canonical map of sessions, calls, tool calls, errors, latency, and
   usage — provider-recorded tokens kept separate from estimates and unknowns.
3. Group traces by task and choose representative replay cases, including
   failures. Never treat a tool attempt as proof of task success.
4. Check the target provider's current capabilities against the workflow.
5. Replay the same cases on source and target, then compare outcomes, tool
   behavior, latency, and usage.
6. Report what passes, what fails, what remains uncertain, and the concrete
   changes needed — apply the switch only on explicit authorization.

## The full harness

The skill guides; the **harness** executes. Install the Python package for the
reproducible pipeline (imports from OTLP/JSONL/Langfuse/Parquet plus local
coding-agent sessions, workflow mapping, replay against OpenAI/Anthropic/
Bedrock/Grok/Groq/local vLLM and more, JSON+HTML migration reports):

```sh
pip install switchit-harness
switchit run --demo     # offline, no credentials
```

The harness also ships an MCP server (`switchit-mcp`, ten tools) so any
MCP-capable agent can drive the pipeline natively.

## Honest boundaries

Workflow labels are triage aids, not measured accuracy. Proposed tool calls
are captured, never executed. A historical model response is reference
behavior, not proof of success. Usage provenance (recorded / estimated /
unknown) stays separated. Full list lives in the harness README and in every
generated report.

## Links

- Python harness: https://pypi.org/project/switchit-harness/
- Repository: https://github.com/switchit-harness/switchit

MIT license.
