#!/usr/bin/env node
/**
 * SwitchIt! CLI (TypeScript port): import → profile → map → cases → replay
 * → report, with a one-command run. Offline by default; live providers
 * require the explicit --remote flag and keys in the environment.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import {
  buildStore,
  makeCall,
  type Store,
} from "./core.js";
import { importClaudeCode, importCodex, importJsonl, readJsonLines } from "./importers.js";
import { buildProfile, groupWorkflows, selectCases } from "./analysis.js";
import { AnthropicAdapter, MockAdapter, OpenAICompatibleAdapter, PROVIDER_PRESETS, RecordedBaselineAdapter, buildAdapter } from "./providers.js";
import { atomicWriteJson, runReplay, RUNNER_VERSION } from "./runner.js";
import { aggregate, buildReport, renderHtml } from "./report.js";

const DEFAULT_STATE_DIR = ".switchit";

function die(message: string): never {
  console.error(message);
  process.exit(2);
}

function loadJson(filePath: string): Record<string, any> {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function storePath(stateDir: string) {
  return path.join(stateDir, "store.json");
}

function requireStore(stateDir: string): Store {
  const filePath = storePath(stateDir);
  if (!fs.existsSync(filePath)) die(`No store at ${filePath}. Run 'import' first.`);
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as Store;
}

async function cmdImport(argv: string[]): Promise<number> {
  const stateDir = flagValue(argv, "--state-dir") ?? DEFAULT_STATE_DIR;
  const demo = argv.includes("--demo");
  const agents = argv.includes("--agents");
  const aiderPaths = collectValues(argv, "--aider");
  const jsonlPaths = collectValues(argv, "--jsonl");
  const inputs = [
    ...jsonlPaths.map((filePath) => ({ kind: "jsonl", load: () => importJsonl(filePath), path: filePath })),
  ];
  if (!inputs.length && !demo && !agents && !aiderPaths.length) {
    die("Nothing to import. Pass --jsonl, --agents, --aider, --devin (or use 'run --demo'). JS v0.1 supports JSONL exports plus Claude Code and Codex session folders.");
  }
  const calls: import("./core.js").CanonicalCall[] = [];
  const sourceFiles: Array<{ importer: string; path: string; records: number }> = [];
  const notes: string[] = [];

  if (demo) {
    const demoPath = new URL("./demo.jsonl", import.meta.url).pathname;
    const raws = await importJsonl(fs.existsSync(demoPath) ? demoPath : path.join(path.dirname(fileURLToPath(import.meta.url)), "demo.jsonl"));
    for (const raw of raws) calls.push(makeCall(raw, "demo", "demo_data"));
    sourceFiles.push({ importer: "demo", path: "demo_data.jsonl", records: raws.length });
    notes.push("Includes the bundled synthetic demo dataset.");
  }
  if (agents) {
    const home = process.env.HOME ?? process.env.USERPROFILE ?? "";
    const claudeDir = path.join(home, ".claude", "projects");
    const codexDir = path.join(home, ".codex", "sessions");
    const sessions: Array<{ agent: string; path: string }> = [];
    if (fs.existsSync(claudeDir)) {
      for (const file of walk(claudeDir, ".jsonl")) {
        if (!path.basename(file).startsWith("skill-")) sessions.push({ agent: "claude-code", path: file });
      }
    }
    if (fs.existsSync(codexDir)) {
      for (const file of walk(codexDir, ".jsonl")) sessions.push({ agent: "codex", path: file });
    }
    const perAgentLimit = Number(flagValue(argv, "--agent-limit") ?? 200);
    const byAgent = new Map<string, number>();
    const limited = sessions.filter((session) => {
      const count = byAgent.get(session.agent) ?? 0;
      byAgent.set(session.agent, count + 1);
      return count < perAgentLimit;
    });
    if (limited.length) {
      const counts = [...byAgent.entries()].map(([agent, count]) => `${agent}: ${count}`).join(", ");
      console.log(`Discovered coding-agent sessions (${counts}); importing…`);
      for (const session of limited) {
        const loader = session.agent === "claude-code" ? importClaudeCode : importCodex;
        try {
          const raws = await loader(session.path);
          for (const raw of raws) calls.push(makeCall(raw, "agents", session.path));
        } catch {
          // one unreadable session must not sink the import
        }
      }
      sourceFiles.push({ importer: "agents", path: "local coding-agent session folders", records: limited.length });
      notes.push("Includes locally recorded coding-agent sessions; same redaction and provenance rules as every other source.");
    } else {
      console.log("No coding-agent session folders found.");
    }
  }
  for (const aiderPath of aiderPaths) {
    const raws = await importJsonl(aiderPath).catch(() => []);
    for (const raw of raws) calls.push(makeCall(raw, "agents", aiderPath));
    sourceFiles.push({ importer: "agents", path: aiderPath, records: raws.length });
  }
  for (const input of inputs) {
    const raws = await input.load();
    for (const raw of raws) calls.push(makeCall(raw, input.kind, input.path));
    sourceFiles.push({ importer: input.kind, path: input.path, records: raws.length });
  }

  calls.sort((a, b) => a.trace_id.localeCompare(b.trace_id) || a.call_index - b.call_index || a.call_id.localeCompare(b.call_id));
  const store = buildStore(calls, { sourceFiles, notes });
  atomicWriteJson(storePath(stateDir), store);
  console.log(`Imported to ${storePath(stateDir)}`);
  console.log(`  traces: ${store.totals.traces}  calls: ${store.totals.calls}`);
  for (const basis of ["recorded", "estimated", "unknown"] as const) {
    const bucket = store.totals.usage[basis];
    console.log(`  usage[${basis}]: calls=${bucket.calls} input=${bucket.input_tokens_known} output=${bucket.output_tokens_known} missing_any=${bucket.calls_missing_either}`);
  }
  return 0;
}

async function cmdProfile(argv: string[]): Promise<number> {
  const stateDir = flagValue(argv, "--state-dir") ?? DEFAULT_STATE_DIR;
  const store = requireStore(stateDir);
  const document = buildProfile(store);
  const profile = document.profile as Record<string, any>;
  atomicWriteJson(path.join(stateDir, "profile.json"), document);
  console.log(`Profile written to ${path.join(stateDir, "profile.json")}`);
  console.log(`  traces: ${profile.totals.traces}  calls: ${profile.totals.calls}  sessions: ${profile.totals.sessions}  errored: ${profile.totals.calls_with_errors}`);
  for (const tool of profile.tools.slice(0, 8)) {
    console.log(`  tool ${tool.name}: ${tool.proposals} proposal(s); args: ${Object.keys(tool.schema_inferred.properties ?? {}).join(", ") || "-"}`);
  }
  if (Object.keys(profile.session_origins ?? {}).length) {
    console.log(`  session origins: ${Object.entries(profile.session_origins).map(([origin, count]) => `${origin}: ${count}`).join(", ")}`);
  }
  return 0;
}

async function cmdMap(argv: string[]): Promise<number> {
  const stateDir = flagValue(argv, "--state-dir") ?? DEFAULT_STATE_DIR;
  const store = requireStore(stateDir);
  const workflowMap = groupWorkflows(store, Number(flagValue(argv, "--threshold") ?? 0.34));
  const outPath = path.join(stateDir, "workflows.json");
  atomicWriteJson(outPath, workflowMap);
  console.log(`Workflow map written to ${outPath}`);
  for (const workflow of workflowMap.workflows as Array<Record<string, any>>) {
    console.log(`  ${workflow.id}  conf=${workflow.confidence.padEnd(6)}  traces=${String(workflow.stats.traces).padStart(3)}  ${workflow.name}`);
  }
  console.log('  Labels are triage aids. Edit workflows.json (set "edited": true) to correct them.');
  return 0;
}

async function cmdCases(argv: string[]): Promise<number> {
  const stateDir = flagValue(argv, "--state-dir") ?? DEFAULT_STATE_DIR;
  const store = requireStore(stateDir);
  const workflowsPath = path.join(stateDir, "workflows.json");
  if (!fs.existsSync(workflowsPath)) die(`No workflow map at ${workflowsPath}. Run 'map' first.`);
  const casesDoc = selectCases(store, JSON.parse(fs.readFileSync(workflowsPath, "utf8")), Number(flagValue(argv, "--max-cases") ?? 24)) as Record<string, any>;
  const outPath = path.join(stateDir, "cases.json");
  atomicWriteJson(outPath, casesDoc);
  console.log(`Replay set written to ${outPath} (${casesDoc.cases.length} cases)`);
  console.log("  Default checks are starting points. Edit cases.json to define real success checks.");
  return 0;
}

function targetAdapter(argv: string[]): { adapter: any; spec: Record<string, any> } {
  const target = flagValue(argv, "--target") ?? "mock";
  const remote = argv.includes("--remote");
  if (target !== "mock" && !remote) {
    die(`Refusing to contact the live '${target}' provider. Remote replay is an explicit opt-in: re-run with --remote (and the provider's key in the environment). Offline target: --target mock.`);
  }
  const config: Record<string, unknown> = {
    model: flagValue(argv, "--target-model"),
    base_url: flagValue(argv, "--base-url"),
    mock_mode: flagValue(argv, "--mock-mode") ?? "mirror",
    timeout: Number(flagValue(argv, "--timeout") ?? 60),
  };
  if (target in PROVIDER_PRESETS) {
    const preset = PROVIDER_PRESETS[target];
    config.api_key = preset.key_envs.map((name) => process.env[name]).find(Boolean) ?? null;
    config.label = target;
    config.model = config.model ?? preset.default_model;
    config.base_url = config.base_url ?? preset.base_url;
    config.key_envs = preset.key_envs;
    return { adapter: new OpenAICompatibleAdapter(config as any, remote), spec: { kind: "openai", preset: target, model: config.model, base_url: config.base_url } };
  }
  switch (target) {
    case "mock":
      return { adapter: new MockAdapter(config.mock_mode as any, config.mock_text as string), spec: { kind: "mock", mock_mode: config.mock_mode } };
    case "openai":
      return {
        adapter: new OpenAICompatibleAdapter({ ...config, api_key: process.env.SWITCHIT_OPENAI_API_KEY ?? process.env.OPENAI_API_KEY ?? null } as any, remote),
        spec: { kind: "openai", model: config.model, base_url: config.base_url },
      };
    case "anthropic":
      return {
        adapter: new AnthropicAdapter({ ...config, api_key: process.env.SWITCHIT_ANTHROPIC_API_KEY ?? process.env.ANTHROPIC_API_KEY ?? null } as any, remote),
        spec: { kind: "anthropic", model: config.model },
      };
    default:
      die(`Unknown target '${target}'. Choose mock, openai, anthropic, or a preset: ${Object.keys(PROVIDER_PRESETS).join(", ")}.`);
  }
}

async function cmdReplay(argv: string[]): Promise<number> {
  const stateDir = flagValue(argv, "--state-dir") ?? DEFAULT_STATE_DIR;
  const runDir = flagValue(argv, "--run-dir") ?? path.join(stateDir, "run");
  const casesPath = path.join(stateDir, "cases.json");
  if (!fs.existsSync(casesPath)) die(`No cases at ${casesPath}. Run 'cases' first.`);
  const casesDoc = loadJson(casesPath);
  if (!casesDoc.cases?.length) die("cases.json contains no cases; nothing to replay.");
  const baseline = new RecordedBaselineAdapter();
  const { adapter: target, spec: targetSpec } = targetAdapter(argv);
  if (argv.includes("--chain")) targetSpec.chain = true;
  const resultsDoc = await runReplay(
    casesDoc,
    new RecordedBaselineAdapter(),
    target,
    { kind: flagValue(argv, "--baseline") ?? "recorded" },
    targetSpec,
    runDir,
    !argv.includes("--fresh"),
  );
  const verdicts: Record<string, number> = {};
  for (const result_ of resultsDoc.case_results as Array<Record<string, any>>) {
    verdicts[result_.verdict] = (verdicts[result_.verdict] ?? 0) + 1;
  }
  console.log(`Replay complete; results in ${path.join(runDir, "results.json")}`);
  console.log(`  verdicts: ${JSON.stringify(verdicts)}`);
  return 0;
}

async function cmdReport(argv: string[]): Promise<number> {
  const stateDir = flagValue(argv, "--state-dir") ?? DEFAULT_STATE_DIR;
  const runDir = flagValue(argv, "--run-dir") ?? path.join(stateDir, "run");
  const resultsPath = path.join(runDir, "results.json");
  if (!fs.existsSync(resultsPath)) die(`No replay results at ${resultsPath}. Run 'replay' first.`);
  const resultsDoc = loadJson(resultsPath);
  const targetKind = (resultsDoc.target_spec?.kind as string) ?? "mock";
  const capabilities = targetKind === "mock" ? new MockAdapter().capabilities() : buildAdapter(targetKind, {}, false).capabilities();
  const summary = aggregate(resultsDoc, loadJson(path.join(stateDir, "cases.json")), capabilities);
  const report = buildReport(resultsDoc, summary);
  atomicWriteJson(path.join(runDir, "report.json"), report);
  fs.writeFileSync(path.join(runDir, "report.html"), renderHtml(report), "utf8");
  console.log(`Report written to ${path.join(runDir, "report.html")} and ${path.join(runDir, "report.json")}`);
  console.log(`  pass=${summary.verdicts.pass} fail=${summary.verdicts.fail} error=${summary.verdicts.error} uncertain=${summary.verdicts.uncertain}`);
  return 0;
}

async function cmdRun(argv: string[]): Promise<number> {
  if (!argv.includes("--demo") && !collectValues(argv, "--jsonl").length && !argv.includes("--agents")) {
    die("Provide inputs (--jsonl/--agents) or pass --demo.");
  }
  await cmdImport(argv);
  await cmdMap(argv);
  await cmdCases(argv);
  await cmdReplay(argv);
  await cmdReport(argv);
  return 0;
}

// ---------------------------------------------------------------------------
// Small argv helpers (keep zero-dep; no commander)
// ---------------------------------------------------------------------------

function flagValue(argv: string[], flag: string): string | undefined {
  const index = argv.indexOf(flag);
  return index >= 0 ? argv[index + 1] : undefined;
}

function collectValues(argv: string[], flag: string): string[] {
  const values: string[] = [];
  for (let index = argv.indexOf(flag); index >= 0; index = argv.indexOf(flag, index + 1)) {
    for (let cursor = index + 1; cursor < argv.length && !argv[cursor].startsWith("--"); cursor += 1) {
      values.push(argv[cursor]);
    }
  }
  return values;
}

function walk(directory: string, suffix: string): string[] {
  const out: string[] = [];
  const stack = [directory];
  while (stack.length) {
    const current = stack.pop()!;
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name.endsWith(suffix)) out.push(full);
    }
  }
  return out.sort();
}

function fileURLToPath(url: string): string {
  return url.startsWith("file://") ? new URL(url).pathname : url;
}

const COMMANDS: Record<string, (argv: string[]) => Promise<number>> = {
  import: cmdImport,
  profile: cmdProfile,
  map: cmdMap,
  cases: cmdCases,
  replay: cmdReplay,
  report: cmdReport,
  run: cmdRun,
};

const command = process.argv[2];
if (!command || !COMMANDS[command]) {
  console.error(`Usage: switchit <${Object.keys(COMMANDS).join("|")}> [flags]`);
  console.error(`Runner ${RUNNER_VERSION}. Live providers need --remote and env keys; offline targets work without credentials.`);
  process.exit(command ? 2 : 0);
}
COMMANDS[command](process.argv.slice(3))
  .then((code) => process.exit(code))
  .catch((error) => {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  });
