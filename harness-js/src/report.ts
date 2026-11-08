/**
 * Report builder: aggregation, JSON report, and the self-contained HTML
 * migration report. Port of switchit/runner.py report half.
 */

import type { ReplayResult } from "./providers.js";
import { BOUNDARIES, VERDICTS } from "./runner.js";

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#x27;");
}

export function aggregate(resultsDoc: Record<string, any>, casesDoc: Record<string, any>, capabilitiesTarget: Record<string, any>) {
  const caseResults = resultsDoc.case_results ?? [];
  const verdicts: Record<string, number> = { pass: 0, fail: 0, error: 0, uncertain: 0 };
  for (const result_ of caseResults) verdicts[result_.verdict] = (verdicts[result_.verdict] ?? 0) + 1;

  const groups = new Map<string, { count: number; case_ids: string[] }>();
  const addGroup = (key: string, caseId: string) => {
    const group = groups.get(key) ?? { count: 0, case_ids: [] };
    group.count += 1;
    if (!group.case_ids.includes(caseId)) group.case_ids.push(caseId);
    groups.set(key, group);
  };
  for (const result_ of caseResults) {
    for (const failure of result_.failures ?? []) {
      const checkType = failure.check?.type;
      addGroup(failure.kind === "target_error" ? "target_error" : `${failure.kind}:${checkType ?? ""}`, result_.case_id);
    }
    for (const finding of result_.tool_findings ?? []) {
      if (["missing_tool", "added_tool", "arg_drift"].includes(finding.kind)) {
        addGroup(`tool_${finding.kind}`, result_.case_id);
      }
    }
  }
  const failureGroups = [...groups.entries()]
    .sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]))
    .map(([key, group]) => ({ key, count: group.count, case_ids: group.case_ids.slice(0, 12) }));

  let needsToolCalling = false;
  for (const testCase of casesDoc.cases ?? []) {
    if ((testCase.historical?.tool_calls ?? []).length || (testCase.request?.tools ?? []).length) needsToolCalling = true;
  }
  const capabilityGaps =
    needsToolCalling && capabilitiesTarget.tool_calling === false
      ? [{ capability: "tool_calling", detail: "cases replay tool-using workflows, but the target adapter declares no tool-calling support" }]
      : [];

  const fixes: Array<Record<string, unknown>> = [];
  if (verdicts.error) {
    fixes.push({
      priority: 1,
      title: `${verdicts.error} case(s) errored on the target provider`,
      case_ids: caseResults.filter((result_: any) => result_.verdict === "error").map((result_: any) => result_.case_id).slice(0, 12),
      suggestion: "Inspect the recorded error strings and the adapter configuration (model id, endpoint, credentials, region) before anything else.",
    });
  }
  if (capabilityGaps.length) {
    fixes.push({
      priority: 2,
      title: "Declared target capabilities do not cover what the cases need",
      case_ids: [],
      suggestion: "Check the target provider's current documentation and either enable the capability or pick workflows that do not need it.",
    });
  }
  if (failureGroups.some((group) => group.key.startsWith("required_check_failed"))) {
    fixes.push({
      priority: 3,
      title: "Required success checks failed on the target",
      case_ids: [],
      suggestion: "For each failing check, decide whether the prompt, the tool schema mapping, or the check itself needs to change; then replay.",
    });
  }
  const missingTools = failureGroups.find((group) => group.key === "tool_missing_tool");
  if (missingTools) {
    fixes.push({
      priority: 4,
      title: "Target stopped proposing tools the reference proposed",
      case_ids: missingTools.case_ids,
      suggestion: "Map tool schemas and system prompts explicitly for the target; missing tool proposals usually trace to prompt or schema translation.",
    });
  }

  const usage = { baseline: mergeResults(caseResults.map((result_: any) => result_.baseline)), target: mergeResults(caseResults.map((result_: any) => result_.target)) };
  return {
    verdicts,
    failure_groups: failureGroups,
    capability_gaps: capabilityGaps,
    prioritized_fixes: fixes,
    usage,
    cases_with_uncertainty: caseResults.filter((result_: any) => (result_.uncertainty ?? []).length).map((result_: any) => result_.case_id),
  };
}

function mergeResults(results: Array<ReplayResult | null>) {
  const merged: Record<string, any> = {
    recorded: { calls: 0, input_tokens_known: 0, output_tokens_known: 0, calls_missing_either: 0 },
    estimated: { calls: 0, input_tokens_known: 0, output_tokens_known: 0, calls_missing_either: 0 },
    unknown: { calls: 0, input_tokens_known: 0, output_tokens_known: 0, calls_missing_either: 0 },
    requests_without_usage: 0,
  };
  for (const result_ of results) {
    if (!result_) continue;
    const usage = result_.usage ?? {};
    const basis = (usage.basis ?? "unknown") as "recorded" | "estimated" | "unknown";
    const bucket = merged[basis in merged ? basis : "unknown"];
    bucket.calls += 1;
    const input = usage.input_tokens ?? null;
    const output = usage.output_tokens ?? null;
    if (input === null && output === null) merged.requests_without_usage += 1;
    for (const [suffix, value] of [["input", input], ["output", output]] as const) {
      if (value === null || value === undefined) bucket[`${suffix}_tokens_known`] += 0;
      else bucket[`${suffix}_tokens_known`] += Number(value);
    }
    if (input === null || output === null) bucket.calls_missing_either += 1;
  }
  return merged;
}

export function buildReport(resultsDoc: Record<string, any>, summary: Record<string, any>, meta: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    report_version: 1,
    generated_at: new Date().toISOString(),
    runner_version: resultsDoc.runner_version,
    fingerprint: resultsDoc.fingerprint,
    baseline_spec: resultsDoc.baseline_spec,
    target_spec: resultsDoc.target_spec,
    verdicts: summary.verdicts,
    capability_gaps: summary.capability_gaps,
    failure_groups: summary.failure_groups,
    prioritized_fixes: summary.prioritized_fixes,
    usage: summary.usage,
    cases: resultsDoc.case_results ?? [],
    boundaries: BOUNDARIES,
    notes: [
      "All historical content in this report was redacted at import time; excerpts are additionally clipped.",
      "Declared adapter capabilities are descriptions to verify against provider documentation, not measured results.",
    ],
    meta,
  };
}

export function renderHtml(report: Record<string, any>): string {
  const esc = escapeHtml;
  const verdicts = report.verdicts as Record<string, number>;
  const total = VERDICTS.reduce((sum, verdict) => sum + (verdicts[verdict] ?? 0), 0);

  const caseCards = (report.cases as Array<Record<string, any>>)
    .map((testCase) => {
      const checks = (testCase.checks_target ?? [])
        .map((check: any) => `<li class="${check.status}"><code>${esc(check.type)}</code> ${check.required ? "required" : "optional"} — ${esc(check.status)}: ${esc(check.detail)}</li>`)
        .join("");
      const uncertainty = (testCase.uncertainty ?? []).map((note: string) => `<li>${esc(note)}</li>`).join("");
      const error = testCase.target?.error ? `<p class="error-text">target error: ${esc(testCase.target.error)}</p>` : "";
      return `<details class="case"><summary><span class="verdict ${esc(testCase.verdict)}">${esc(testCase.verdict)}</span> ${esc(testCase.case_id)} <span class="muted">— workflow ${esc(String(testCase.workflow_id))}, trace ${esc(String(testCase.trace_id))}</span></summary><div class="case-body"><h4>Checks (target)</h4><ul>${checks || "<li>none</li>"}</ul><h4>Uncertainty</h4><ul>${uncertainty || "<li>none</li>"}</ul><p><strong>baseline:</strong> ${esc((testCase.baseline?.text ?? "").slice(0, 300))}</p><p><strong>target:</strong> ${esc((testCase.target?.text ?? "").slice(0, 300))}</p></div></details>`;
    })
    .join("");

  const fixes = (report.prioritized_fixes as Array<Record<string, any>>)
    .map((fix) => `<li><strong>P${fix.priority} — ${esc(fix.title)}</strong><br>${esc(fix.suggestion)}</li>`)
    .join("");
  const boundaries = (report.boundaries as string[]).map((boundary) => `<li>${esc(boundary)}</li>`).join("");

  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>SwitchIt! migration report</title>
<style>
body { font-family: ui-monospace, Menlo, Consolas, monospace; margin: 2rem auto; max-width: 1080px; padding: 0 1.5rem; background: #fafafa; color: #333; line-height: 1.55; }
.pass, .v-pass { color: #1e7d3c; } .fail, .c-fail { color: #b3541e; } .error, .v-error { color: #b03030; } .uncertain, .v-uncertain { color: #8a6d00; }
.muted { color: #777; } details.case { border: 1px solid #bbb; border-radius: 6px; padding: .5rem .9rem; margin: .5rem 0; }
summary { cursor: pointer; font-weight: 600; } .verdict { border: 1px solid currentColor; border-radius: 4px; padding: 0 6px; }
</style></head><body>
<h1>SWITCHIT! / MIGRATION REPORT</h1>
<p class="muted">generated ${esc(report.generated_at)} · runner ${esc(String(report.runner_version))} · fingerprint ${esc(String(report.fingerprint).slice(0, 16))}…</p>
<h2>Summary</h2><p>${total} cases — pass ${verdicts.pass ?? 0} · fail ${verdicts.fail ?? 0} · error ${verdicts.error ?? 0} · uncertain ${verdicts.uncertain ?? 0}</p>
<h2>Prioritized fixes</h2><ol>${fixes || "<li>none</li>"}</ol>
<h2>Cases</h2>${caseCards}
<h2>Boundaries</h2><ul>${boundaries}</ul>
</body></html>`;
}
