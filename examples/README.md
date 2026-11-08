# Examples

Everything in this folder is **synthetic**: invented retail-support traces
written for this repository so the harness can be exercised end to end without
any real data, external credentials, or network access. Nothing here is
derived from any assessment or third-party dataset.

| File | What it is | Importer |
|---|---|---|
| `traces.jsonl` | 16 calls across 6 traces / 3 workflows, mixed usage provenance (`source_reported`, `estimated`, `unknown`), one failed call, one message with a fake email + phone so redaction is visible | `--jsonl` |
| `otlp_traces.json` | OTLP JSON with two `gen_ai` LLM spans and one plain HTTP span (the importer must skip the non-LLM span) | `--otlp` |
| `langfuse_export.json` | Langfuse-style export: two GENERATION observations (one ERROR level) on one trace | `--langfuse` |
| `app_config.json` | The app-configuration input: system prompt, three tool schemas, current provider options | `--app-config` |

## Try them

```sh
python3 -m switchit.cli import --jsonl examples/traces.jsonl --app-config examples/app_config.json --state-dir /tmp/switchit-example
python3 -m switchit.cli map --state-dir /tmp/switchit-example
python3 -m switchit.cli cases --state-dir /tmp/switchit-example
python3 -m switchit.cli replay --state-dir /tmp/switchit-example            # offline: recorded baseline vs mock target
python3 -m switchit.cli report --state-dir /tmp/switchit-example
open /tmp/switchit-example/run/report.html
```

Or the one-command version:

```sh
python3 -m switchit.cli run --jsonl examples/traces.jsonl --app-config examples/app_config.json --state-dir /tmp/switchit-example
```

The other importers work the same way: add `--otlp examples/otlp_traces.json`
and/or `--langfuse examples/langfuse_export.json` to the `import` command.

Live providers are covered in the top-level README; they require `--remote`
and an environment key, and are never exercised by these examples.
