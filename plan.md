# SwitchIt! — implementation plan

SwitchIt! helps a team move an existing AI workflow to another provider and verify that the workflows people rely on still behave acceptably. The [system map](docs/system-map.svg) shows the components; the [migration run](docs/migration-run.svg) shows their order.

## User path

1. Import authorized traces and the current app's prompt, tool, and provider configuration.
2. Normalize calls into linked sessions and traces. Preserve original IDs, errors, latency, and token provenance (`recorded`, `estimated`, `unknown`). Redact secrets before examples enter reports.
3. Group traces into provisional workflows, show representative evidence and uncertainty, and allow the user to correct names or assignments.
4. Freeze a small replay set covering common, costly, risky, and failed workflows. Add explicit success checks where outcomes matter; a historical model response alone is not proof of success.
5. Replay the same cases through current and candidate provider adapters. Capture text, proposed tool calls, latency, usage, and errors. Never execute live business tools from historical traces by default.
6. Compare checks and tool behavior. Flag missing capabilities, changed outputs, regressions, and unknown results. Produce a local HTML and JSON migration report with a prioritized fix list.

## Connectors

- **Inputs:** OpenTelemetry/OTLP JSON, JSON/JSONL, Parquet (optional dependency), and Langfuse-style exports; a simple app-configuration JSON supplies prompts and tool definitions.
- **Providers:** recorded baseline and mock adapters for offline work; OpenAI-compatible HTTP (including local vLLM), Anthropic Messages, and Amazon Bedrock Converse adapters for live tests.
- **Secrets:** environment variables only. Remote replay is an explicit CLI option; local import and analysis do not contact a provider.

## Code layout

```text
switchit/
  core.py        import, normalize, redact, workflow map, replay selection
  providers.py   provider adapters and capability descriptions
  runner.py      replay, compare, report, checkpoints
  cli.py         import → map → cases → replay → report; one-command run
  demo_data.py   bundled synthetic dataset for --demo and offline tests
tests/           synthetic end-to-end and connector-shape checks
examples/        synthetic traces and offline provider config
skills/switchit/ SKILL.md: guide the agent through evidence and migration decisions
```

## Implementation stages

- [x] Canonical trace schema and importers; source ID and usage reconciliation.
- [x] Provisional workflow grouping with evidence and confidence signals.
- [x] Representative case selection and editable success checks.
- [x] Recorded/mock provider adapters and a complete offline replay loop.
- [x] Live provider adapters with explicit data-transfer opt-in.
- [x] JSON and HTML comparison report, failure grouping, and resumable runs.
- [x] Update the `$switchit` skill and README with actual harness commands.
- [x] Validate on synthetic data: every trace and token counted once, failures visible, no secret in output, and a full run works without external credentials.

## Boundaries

Workflow labels and similarity scores are triage aids, not measured accuracy. For a workflow that needs a real tool to complete, a single-call replay can verify the proposed tool request but must leave the end-to-end outcome unverified until a safe tool fixture or authorized test environment exists. The Hyde assessment dataset and results are not inputs to this project.
