/**
 * Runner: replay, check evaluation, tool comparison, verdicts, checkpoints,
 * aggregation, and the HTML/JSON migration report. Port of switchit/runner.py.
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import type { CanonicalResponse } from "./core.js";
import type { CaseLike, ReplayRequest, ReplayResult } from "./providers.js";

export const RUNNER_VERSION = "0.1.0";
export const MAX_CHAIN_TURNS = 3;
export const VERDICTS = ["pass", "fail", "error", "uncertain"] as const;
export type Verdict = (typeof VERDICTS)[number];

export const BOUNDARIES = [
  "Workflow labels and similarity scores are triage aids, not measured accuracy.",
  "Proposed tool calls are captured for comparison; the harness never executes a business tool.",
  "A historical model response is reference behavior, not proof of success; checks define success explicitly.",
  "Token usage is kept separate by provenance: recorded (source-reported), estimated, and unknown. These are telemetry, not invoices.",
  "End-to-end outcomes for workflows that need a real tool remain unverified until a safe tool fixture or authorized test environment exists.",
];

export function atomicWriteJson(filePath: string, data: unknown): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temp = `${filePath}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data, null, 1), "utf8");
  fs.renameSync(temp, filePath);
}

export function runFingerprint(casesDoc: unknown, baselineSpec: unknown, targetSpec: unknown): string {
  return crypto
    .createHash("sha256")
    .update(JSON.stringify({ runner_version: RUNNER_VERSION, cases: casesDoc, baseline: baselineSpec, target: targetSpec }))
    .digest("hex");
}

function clip(text: string | null | undefined, limit = 600): string {
  const value = text ?? "";
  if (value.length <= limit) return value;
  return `${value.slice(0, Math.floor(limit * 0.75))} … ${value.slice(-Math.floor(limit * 0.25))}`;
}

function deepSubset(superset: unknown, subset: unknown): boolean {
  if (Array.isArray(subset)) {
    if (!Array.isArray(superset) || superset.length < subset.length) return false;
    const remaining = [...superset];
    for (const item of subset) {
      const index = remaining.findIndex((candidate) => deepSubset(candidate, item));
      if (index === -1) return false;
      remaining.splice(index, 1);
    }
    return true;
  }
  if (subset && typeof subset === "object") {
    if (!superset || typeof superset !== "object") return false;
    return Object.entries(subset as Record<string, unknown>).every(([key, value]) =>
      key in (superset as Record<string, unknown>) && deepSubset((superset as Record<string, unknown>)[key], value),
    );
  }
  return superset === subset;
}

export function evaluateCheck(check: Record<string, any>, result_: ReplayResult): Record<string, unknown> {
  const evaluated: Record<string, unknown> = {
    type: check.type,
    required: Boolean(check.required),
    source: check.source ?? "user",
    status: "unknown",
    detail: "",
  };
  const error = result_.error;
  const text = result_.text ?? "";
  const toolNames = (result_.tool_calls ?? []).map((call) => call.name);

  if (error) {
    if (check.type === "no_error") evaluated.status = "fail", (evaluated.detail = clip(error, 200));
    else evaluated.detail = "target call errored; check not evaluable";
    return evaluated;
  }
  switch (check.type) {
    case "no_error":
      Object.assign(evaluated, { status: "pass", detail: "completed without a provider error" });
      break;
    case "tool_called": {
      const name = check.name ?? "";
      if (toolNames.includes(name)) evaluated.status = "pass", (evaluated.detail = `proposed tool '${name}'`);
      else evaluated.status = "fail", (evaluated.detail = `tool '${name}' was not proposed; proposed: ${toolNames.join(", ") || "none"}`);
      break;
    }
    case "tool_args_include": {
      const match = (result_.tool_calls ?? []).some(
        (call) => call.name === check.name && deepSubset(call.arguments ?? {}, check.arguments ?? {}),
      );
      evaluated.status = match ? "pass" : "fail";
      evaluated.detail = match ? "arguments include the required subset" : `no '${check.name}' call includes the required subset`;
      break;
    }
    case "tool_calls_equal": {
      const actual = toolNames;
      const expected = check.names ?? [];
      evaluated.status = JSON.stringify(actual) === JSON.stringify(expected) ? "pass" : "fail";
      evaluated.detail = `expected ${JSON.stringify(expected)}; got ${JSON.stringify(actual)}`;
      break;
    }
    case "output_contains":
      evaluated.status = text.toLowerCase().includes(String(check.text ?? "").toLowerCase()) ? "pass" : "fail";
      evaluated.detail = evaluated.status === "pass" ? "output contains the substring" : "substring not found";
      break;
    case "output_regex": {
      try {
        evaluated.status = new RegExp(check.pattern ?? "").test(text) ? "pass" : "fail";
      } catch (regexError) {
        evaluated.status = "unknown";
        evaluated.detail = `invalid regex: ${(regexError as Error).message}`;
      }
      break;
    }
    case "output_min_chars": {
      const minimum = Number(check.value ?? 0);
      evaluated.status = text.length >= minimum ? "pass" : "fail";
      evaluated.detail = `output length ${text.length} vs minimum ${minimum}`;
      break;
    }
    case "latency_ms_max": {
      const latency = result_.latency_ms;
      if (latency === null) evaluated.detail = "no latency recorded";
      else {
        const maximum = Number(check.value ?? 0);
        evaluated.status = latency <= maximum ? "pass" : "fail";
        evaluated.detail = `latency ${latency} ms vs maximum ${maximum} ms`;
      }
      break;
    }
    case "usage_input_max":
    case "usage_output_max": {
      const key = check.type === "usage_input_max" ? "input" : "output";
      const value = (result_.usage as Record<string, any>)[`${key}_tokens`];
      if (value === null || value === undefined || result_.usage.basis === "unknown") {
        evaluated.detail = `target usage for '${key}' is unknown`;
      } else {
        const maximum = Number(check.value ?? 0);
        evaluated.status = value <= maximum ? "pass" : "fail";
        evaluated.detail = `${key} tokens ${value} vs maximum ${maximum} (provenance: ${result_.usage.basis})`;
      }
      break;
    }
    default:
      evaluated.detail = `unknown check type ${check.type}`;
  }
  return evaluated;
}

function evaluateChecks(checks: Array<Record<string, any>>, result_: ReplayResult) {
  return (checks ?? []).map((check) => evaluateCheck(check, result_));
}

function historicalResult(testCase: CaseLike): ReplayResult {
  const historical = testCase.historical;
  return {
    text: historical.text ?? "",
    tool_calls: historical.tool_calls ?? [],
    finish_reason: historical.finish_reason,
    error: historical.error,
    latency_ms: historical.latency_ms ?? 0,
    usage: { input_tokens: historical.usage?.input_tokens ?? null, output_tokens: historical.usage?.output_tokens ?? null, basis: historical.usage?.basis ?? "unknown", estimator: historical.usage?.estimator ?? null },
    adapter: "historical",
    model: historical.model,
  };
}

function compareTools(baseline: Array<{ name: string; arguments: Record<string, unknown> }>, target: Array<{ name: string; arguments: Record<string, unknown> }>) {
  const findings: Array<Record<string, unknown>> = [];
  const baselineByName = new Map((baseline ?? []).map((call) => [call.name, call]));
  const targetByName = new Map((target ?? []).map((call) => [call.name, call]));
  for (const name of [...baselineByName.keys()].sort()) {
    if (targetByName.has(name)) {
      const same = JSON.stringify(baselineByName.get(name)!.arguments) === JSON.stringify(targetByName.get(name)!.arguments);
      findings.push({ kind: same ? "match" : "arg_drift", tool: name, detail: same ? "same tool, equivalent arguments" : "arguments drifted" });
    } else {
      findings.push({ kind: "missing_tool", tool: name, detail: `reference proposed '${name}' but the target did not` });
    }
  }
  for (const name of [...targetByName.keys()].sort()) {
    if (!baselineByName.has(name)) {
      findings.push({ kind: "added_tool", tool: name, detail: `target proposed '${name}' but the reference did not` });
    }
  }
  if (!findings.length) findings.push({ kind: "match", tool: "", detail: "no proposed tool calls on either side" });
  return findings;
}

function chainTargetTurns(testCase: CaseLike & { tool_fixtures?: Record<string, string> }, targetAdapter: { replay: (request: ReplayRequest, testCase?: CaseLike) => Promise<ReplayResult> | ReplayResult }, maxTurns = MAX_CHAIN_TURNS) {
  return (async () => {
    const fixtures = testCase.tool_fixtures ?? {};
    const messages: Array<any> = [...(testCase.request.messages ?? [])];
    const tools: Array<any> = [...(testCase.request.tools ?? [])];
    const fixturesUsed: string[] = [];
    const fixturesMissing: string[] = [];
    const proposals: Array<{ name: string; arguments: Record<string, unknown> }> = [];
    const turns: Array<Record<string, unknown>> = [];
    let final: ReplayResult | null = null;

    for (let turn = 0; turn < maxTurns; turn += 1) {
      const result_ = await targetAdapter.replay({ messages, tools }, testCase as CaseLike);
      proposals.push(...(result_.tool_calls ?? []));
      turns.push({
        turn: turn + 1,
        text: clip(result_.text, 300),
        tool_calls: (result_.tool_calls ?? []).map((call) => call.name),
        error: result_.error,
        usage: result_.usage,
        latency_ms: result_.latency_ms,
      });
      final = result_;
      if (result_.error || !result_.tool_calls?.length) break;
      messages.push({ role: "assistant", content: result_.text ?? "", tool_calls: result_.tool_calls });
      result_.tool_calls.forEach((call, position) => {
        let fixtureText: string;
        if (call.name && fixtures[call.name]) {
          fixtureText = fixtures[call.name];
          if (!fixturesUsed.includes(call.name)) fixturesUsed.push(call.name);
        } else {
          fixtureText = `[switchit fixture] no recorded result for tool '${call.name}'; the historical trace never used it.`;
          if (call.name && !fixturesMissing.includes(call.name)) fixturesMissing.push(call.name);
        }
        messages.push({ role: "tool", content: fixtureText, tool_call_id: `call_${turn}_${position}`, name: call.name });
      });
    }
    return {
      final: final!,
      meta: { turns, proposals, fixtures_used: fixturesUsed, fixtures_missing: fixturesMissing, max_turns: maxTurns },
    };
  })();
}

export async function replayCase(
  testCase: CaseLike & { tool_fixtures?: Record<string, string>; checks: Array<Record<string, any>>; case_id: string; workflow_id?: string; trace_id?: string; origins?: string[]; notes?: string[] },
  baselineAdapter: { replay: (request: ReplayRequest, testCase?: CaseLike) => Promise<ReplayResult> | ReplayResult },
  targetAdapter: { replay: (request: ReplayRequest, testCase?: CaseLike) => Promise<ReplayResult> | ReplayResult },
  chain = false,
): Promise<Record<string, unknown>> {
  const request = testCase.request;
  const baselineResult = await baselineAdapter.replay(request, testCase);
  let targetResult: ReplayResult;
  let chainMeta: Record<string, unknown> | null = null;

  if (chain && testCase.tool_fixtures && Object.keys(testCase.tool_fixtures).length) {
    const chained = await chainTargetTurns(testCase, targetAdapter);
    targetResult = chained.final;
    chainMeta = chained.meta;
    const mergedCalls: Array<{ name: string; arguments: Record<string, unknown> }> = [];
    const seen = new Set<string>();
    for (const call of chained.meta.proposals) {
      if (call.name && !seen.has(call.name)) {
        seen.add(call.name);
        mergedCalls.push(call);
      }
    }
    targetResult = { ...targetResult, tool_calls: mergedCalls };
  } else {
    targetResult = await targetAdapter.replay(request, testCase);
  }

  const checksTarget = evaluateChecks(testCase.checks, targetResult);
  const checksReference = evaluateChecks(testCase.checks, historicalResult(testCase));
  const referenceByType = new Map(checksReference.map((check) => [check.type as string, check]));
  const failures: Array<Record<string, unknown>> = [];
  const uncertainty: string[] = [];
  const notes: string[] = [];

  for (const check of checksTarget) {
    const reference = referenceByType.get(check.type as string);
    if (check.status === "fail") {
      failures.push({
        kind: check.required ? "required_check_failed" : "optional_check_failed",
        check,
        case_id: testCase.case_id,
      });
      if (reference?.status === "fail") {
        uncertainty.push(
          `check '${check.type}' also fails against the historical reference; the check may be mis-specified rather than the target broken`,
        );
      }
    } else if (check.status === "unknown" && check.required) {
      uncertainty.push(`required check '${check.type}' could not be evaluated: ${check.detail}`);
    }
  }
  if (targetResult.error) failures.push({ kind: "target_error", check: null, case_id: testCase.case_id });

  const explicitToolChecks = new Set(
    (testCase.checks ?? []).filter((check) => check.type === "tool_called").map((check) => check.name),
  );
  const toolFindings = compareTools(historicalResult(testCase).tool_calls, targetResult.tool_calls ?? []);
  for (const finding of toolFindings) {
    if (finding.kind === "missing_tool" && !explicitToolChecks.has(finding.tool as string)) {
      uncertainty.push(`reference proposed tool '${finding.tool}' but the target did not, and no explicit check covers it`);
    } else if (finding.kind === "added_tool") {
      uncertainty.push(`target proposed an additional tool '${finding.tool}' absent from the reference`);
    } else if (finding.kind === "arg_drift") {
      uncertainty.push(`arguments drifted for tool '${finding.tool}' while no strict check covers them`);
    }
  }
  const historicalBasis = testCase.historical.usage?.basis;
  const targetBasis = targetResult.usage?.basis;
  if (historicalBasis !== targetBasis) {
    notes.push(
      `usage provenance differs: historical '${historicalBasis}' vs target '${targetBasis}'; token totals are reported separately by basis and are not directly comparable`,
    );
  }

  const verdict: Verdict = targetResult.error
    ? "error"
    : failures.some((failure) => failure.kind === "required_check_failed")
      ? "fail"
      : uncertainty.length
        ? "uncertain"
        : "pass";

  const caseResult: Record<string, unknown> = {
    case_id: testCase.case_id,
    workflow_id: testCase.workflow_id,
    trace_id: testCase.trace_id,
    origins: testCase.origins ?? [],
    baseline: baselineResult,
    target: targetResult,
    checks_target: checksTarget,
    checks_reference: checksReference,
    tool_findings: toolFindings,
    failures,
    uncertainty,
    notes,
    verdict,
  };
  if (chainMeta) {
    caseResult.chain = chainMeta;
    const missing = chainMeta.fixtures_missing as string[];
    if (missing.length) {
      uncertainty.push(`target proposed tool(s) with no recorded fixture (${missing.join(", ")}); the continuation received a placeholder result`);
    }
    uncertainty.push(
      `chained replay used recorded tool fixtures (${(chainMeta.fixtures_used as string[]).join(", ") || "none"}); end-to-end validity depends on fixture fidelity, and no real business tool was executed`,
    );
  }
  return caseResult;
}

export function loadCheckpoint(runDir: string): Record<string, any> | null {
  const filePath = path.join(runDir, "run_state.json");
  if (!fs.existsSync(filePath)) return null;
  try {
    return JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch {
    return null;
  }
}

export async function runReplay(
  casesDoc: Record<string, any>,
  baselineAdapter: { replay: (request: ReplayRequest, testCase?: CaseLike) => Promise<ReplayResult> | ReplayResult },
  targetAdapter: { replay: (request: ReplayRequest, testCase?: CaseLike) => Promise<ReplayResult> | ReplayResult },
  baselineSpec: Record<string, unknown>,
  targetSpec: Record<string, unknown>,
  runDir: string,
  resume = true,
): Promise<Record<string, unknown>> {
  fs.mkdirSync(runDir, { recursive: true });
  const fingerprint = runFingerprint(casesDoc, baselineSpec, targetSpec);
  let state = resume ? loadCheckpoint(runDir) : null;
  if (state && state.fingerprint !== fingerprint) {
    throw new Error(
      "Run state does not match the current cases, adapters, or runner version. Use a fresh run directory or restore the matching cases and adapter settings.",
    );
  }
  if (!state) {
    state = {
      runner_version: RUNNER_VERSION,
      fingerprint,
      started_at: new Date().toISOString(),
      baseline_spec: baselineSpec,
      target_spec: targetSpec,
      completed: {},
    };
  }
  const chain = Boolean(targetSpec.chain);
  const results: Array<Record<string, unknown>> = [];
  for (const testCase of casesDoc.cases ?? []) {
    if (state.completed[testCase.case_id]) {
      results.push(state.completed[testCase.case_id]);
      continue;
    }
    const caseResult = await replayCase(testCase, baselineAdapter, targetAdapter, chain);
    state.completed[testCase.case_id] = caseResult;
    state.updated_at = new Date().toISOString();
    atomicWriteJson(path.join(runDir, "run_state.json"), state);
    results.push(caseResult);
  }
  const document = {
    runner_version: RUNNER_VERSION,
    fingerprint,
    started_at: state.started_at,
    finished_at: new Date().toISOString(),
    baseline_spec: baselineSpec,
    target_spec: targetSpec,
    case_results: results,
  };
  atomicWriteJson(path.join(runDir, "results.json"), document);
  return document;
}
