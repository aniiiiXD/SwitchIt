/**
 * Analysis: TF-IDF workflow grouping, replay-case selection with default
 * checks, and the session profile. Port of switchit/core.py analysis half.
 */

import type { CanonicalCall, Store, Trace, Usage } from "./core.js";
import { emptyUsage, excerpt, mergeUsage, redactValue } from "./core.js";

// ---------------------------------------------------------------------------
// TF-IDF + greedy clustering
// ---------------------------------------------------------------------------

const STOPWORDS = new Set([
  "the", "and", "for", "with", "this", "that", "you", "your", "are", "was", "were",
  "can", "could", "would", "should", "have", "has", "had", "not", "but", "all",
  "please", "need", "want", "from", "into", "about", "there", "their", "them",
  "they", "what", "when", "where", "which", "who", "how", "why", "will", "just",
  "get", "got", "does", "did", "done", "any", "our", "out", "too", "also", "very",
]);

export function tokenize(text: string): string[] {
  return (text ?? "")
    .toLowerCase()
    .match(/[a-z][a-z0-9_]{2,}/g)
    ?.filter((token) => !STOPWORDS.has(token) && token.length < 32) ?? [];
}

function cosine(a: Record<string, number>, b: Record<string, number>): number {
  const [small, large] = Object.keys(a).length <= Object.keys(b).length ? [a, b] : [b, a];
  let sum = 0;
  for (const [token, value] of Object.entries(small)) sum += value * (large[token] ?? 0);
  return sum;
}

function toolJaccard(a: string[], b: string[]): number {
  const setA = new Set(a);
  const setB = new Set(b);
  const union = new Set([...setA, ...setB]);
  return union.size ? [...setA].filter((x) => setB.has(x)).length / union.size : 0;
}

export interface Workflow {
  id: string;
  name: string;
  description: string;
  trace_ids: string[];
  tool_names: string[];
  evidence: { example_trace_ids: string[]; user_requests: Array<{ trace_id: string; text: string }> };
  confidence: "high" | "medium" | "low";
  uncertainty: string[];
  evidence_level: string;
  label_source: string;
  edited: boolean;
  stats: {
    traces: number;
    calls: number;
    error_traces: number;
    usage: Usage;
    share_of_traces: number;
    representative_latency_ms: number | null;
  };
}

export function groupWorkflows(store: Store, threshold = 0.34): Record<string, unknown> {
  const traces = store.traces;
  const documents = traces.map((trace) =>
    trace.user_requests.length ? trace.user_requests.join(" ") : trace.tool_names.join(" "),
  );

  // TF-IDF vectors.
  const documentFrequency = new Map<string, number>();
  const tokenized = documents.map(tokenize);
  for (const tokens of tokenized) {
    for (const token of new Set(tokens)) documentFrequency.set(token, (documentFrequency.get(token) ?? 0) + 1);
  }
  const total = Math.max(1, documents.length);
  const idf = new Map([...documentFrequency.entries()].map(([token, count]) => [token, Math.log((1 + total) / (1 + count)) + 1]));
  const vectors = tokenized.map((tokens) => {
    const counts = new Map<string, number>();
    for (const token of tokens) counts.set(token, (counts.get(token) ?? 0) + 1);
    const vector: Record<string, number> = {};
    let norm = 0;
    for (const [token, count] of counts) {
      const value = (1 + Math.log(count)) * (idf.get(token) ?? 1);
      vector[token] = value;
      norm += value * value;
    }
    norm = Math.sqrt(norm);
    if (norm > 0) for (const token of Object.keys(vector)) vector[token] /= norm;
    return vector;
  });

  const clusters: Array<{ members: number[] }> = [];
  traces.forEach((trace, position) => {
    let bestCluster: { members: number[] } | null = null;
    let bestScore = 0;
    for (const cluster of clusters) {
      const representative = Math.max(
        ...cluster.members.map((member) => cosine(vectors[position], vectors[member])),
      );
      const first = traces[cluster.members[0]];
      const toolScore = toolJaccard(trace.tool_names, first.tool_names);
      const score =
        !trace.tool_names.length && !first.tool_names.length
          ? representative
          : 0.65 * representative + 0.35 * toolScore;
      if (score > bestScore) {
        bestCluster = cluster;
        bestScore = score;
      }
    }
    if (bestCluster && bestScore >= threshold) bestCluster.members.push(position);
    else clusters.push({ members: [position] });
  });

  const workflows: Workflow[] = [];
  clusters.forEach((cluster, clusterPosition) => {
    const members = cluster.members.map((index) => traces[index]);
    const memberVectors = cluster.members.map((index) => vectors[index]);
    const toolCounter = new Map<string, number>();
    for (const trace of members) for (const name of trace.tool_names) toolCounter.set(name, (toolCounter.get(name) ?? 0) + 1);
    const termMass = new Map<string, number>();
    for (const vector of memberVectors) {
      for (const [token, value] of Object.entries(vector)) termMass.set(token, (termMass.get(token) ?? 0) + value);
    }
    const topTerms = [...termMass.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3).map(([term]) => term);
    const topTools = [...toolCounter.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3).map(([name]) => name);

    const parts: string[] = [];
    if (topTerms.length) parts.push(topTerms.map((term) => term[0].toUpperCase() + term.slice(1)).join(" "));
    if (topTools.length) parts.push(topTools[0]);
    const name = parts.length ? parts.join(" — ") : `Unlabeled activity ${clusterPosition + 1}`;

    const usage = mergeUsage(...members.map((trace) => trace.usage));
    const callCount = members.reduce((sum, trace) => sum + trace.call_count, 0);
    const errorTraces = members.filter((trace) => trace.error_count).length;
    const evidenceRequests = members.flatMap((trace) =>
      trace.user_requests.slice(0, 2).map((text) => ({ trace_id: trace.trace_id, text: excerpt(text, 1200) })),
    ).slice(0, 5);
    const evidenceLevel =
      members.length === 1
        ? members[0].user_requests.length
          ? "user_request"
          : members[0].tool_names.length
            ? "tool_call"
            : "assistant_only"
        : members.some((trace) => trace.user_requests.length)
          ? "user_request"
          : "tool_call";

    const uncertainties: string[] = [];
    if (members.length === 1) uncertainties.push("single_trace_cluster");
    if (evidenceLevel !== "user_request") uncertainties.push("no_user_request_evidence");
    if (errorTraces) uncertainties.push("contains_failed_calls");

    let meanIntra = 1;
    if (memberVectors.length > 1) {
      let sum = 0;
      let pairs = 0;
      for (let i = 0; i < memberVectors.length; i += 1) {
        for (let j = i + 1; j < memberVectors.length; j += 1) {
          sum += cosine(memberVectors[i], memberVectors[j]);
          pairs += 1;
        }
      }
      meanIntra = sum / pairs;
      if (meanIntra < 0.2) uncertainties.push("low_internal_similarity");
    }
    const confidence =
      members.length >= 2 && evidenceLevel === "user_request" && meanIntra >= 0.2
        ? "high"
        : members.length >= 2 || evidenceLevel === "user_request"
          ? "medium"
          : "low";

    workflows.push({
      id: `wf_${String(clusterPosition + 1).padStart(3, "0")}`,
      name: name.slice(0, 80),
      description: `Provisional group of ${members.length} trace(s) around terms: ${topTerms.join(", ") || "(no dominant terms)"}${topTools.length ? `; tools: ${topTools.join(", ")}` : ""}.`,
      trace_ids: members.map((trace) => trace.trace_id).sort(),
      tool_names: [...toolCounter.keys()].sort(),
      evidence: {
        example_trace_ids: members.slice(0, 5).map((trace) => trace.trace_id),
        user_requests: evidenceRequests,
      },
      confidence,
      uncertainty: uncertainties,
      evidence_level: evidenceLevel,
      label_source: "heuristic",
      edited: false,
      stats: {
        traces: members.length,
        calls: callCount,
        error_traces: errorTraces,
        usage,
        share_of_traces: Number((members.length / Math.max(1, traces.length)).toFixed(4)),
        representative_latency_ms: members.reduce<number | null>(
          (max, trace) => (trace.latency_ms_max !== null && (max === null || trace.latency_ms_max > max) ? trace.latency_ms_max : max),
          null,
        ),
      },
    });
  });

  const assigned = new Set(workflows.flatMap((workflow) => workflow.trace_ids));
  return {
    workflow_map_version: 1,
    params: { similarity_threshold: threshold },
    workflows,
    unassigned_trace_ids: traces.filter((trace) => !assigned.has(trace.trace_id)).map((trace) => trace.trace_id),
    notes: [
      "Workflow labels and similarity scores are triage aids, not measured accuracy.",
      "Names are derived from dominant TF-IDF terms and tool names; a reviewer (human or agent) should correct them.",
    ],
  };
}

// ---------------------------------------------------------------------------
// Replay-case selection
// ---------------------------------------------------------------------------

export interface ReplayCase {
  case_id: string;
  workflow_id: string;
  trace_id: string;
  origins: string[];
  request: { messages: Array<{ role: string; content: string }>; tools: Array<{ name: string }> };
  historical: {
    text: string;
    tool_calls: Array<{ name: string; arguments: Record<string, unknown> }>;
    finish_reason: string | null;
    error: string | null;
    usage: { input_tokens: number | null; output_tokens: number | null; basis: string; estimator: string | null };
    latency_ms: number | null;
    model: string | null;
    call_id: string | null;
    usage_note: string;
  };
  checks: Array<Record<string, unknown>>;
  tool_fixtures?: Record<string, string>;
  notes: string[];
}

function caseUsageValue(usage: unknown, key: "input" | "output"): number | null {
  if (!usage || typeof usage !== "object") return null;
  const record = usage as Record<string, any>;
  // Canonical call-usage shape: {input_tokens, output_tokens, basis}.
  if (record[`${key}_tokens`] !== undefined) {
    const direct = record[`${key}_tokens`];
    return direct === null || direct === undefined ? null : Number(direct) || null;
  }
  // Aggregate shape: {recorded: {input_tokens_known, ...}, ...}.
  for (const basis of ["recorded", "estimated"]) {
    const value = record[basis]?.[`${key}_tokens_known`];
    if (value) return value;
  }
  return null;
}

export function selectCases(store: Store, workflowMap: Record<string, any>, maxCases = 24): Record<string, unknown> {
  const tracesById = new Map(store.traces.map((trace) => [trace.trace_id, trace]));
  const callsById = new Map(store.calls.map((call) => [call.call_id, call]));
  const cases: ReplayCase[] = [];
  const usedTraces = new Set<string>();

  const makeCase = (trace: Trace, workflowId: string, origins: string[]): ReplayCase | null => {
    const members = trace.call_ids.map((id) => callsById.get(id)!).filter(Boolean);
    if (!members.length) return null;
    const withResponse = members.filter((call) => call.response.text || call.response.tool_calls.length);
    const withoutError = withResponse.filter((call) => !call.response.error);
    const seed = withoutError.at(-1) ?? withResponse.at(-1) ?? members.at(-1)!;

    const mergedTools: Array<{ name: string; arguments: Record<string, unknown> }> = [];
    const seenTools = new Set<string>();
    const toolFixtures: Record<string, string> = {};
    for (let position = 0; position < members.length; position += 1) {
      const call = members[position];
      const proposed = call.response.tool_calls.filter((toolCall) => toolCall.name);
      for (const toolCall of proposed) {
        if (!seenTools.has(toolCall.name)) {
          seenTools.add(toolCall.name);
          mergedTools.push(toolCall);
        }
      }
      if (position + 1 < members.length) {
        const followup = members[position + 1];
        const results = followup.request.messages
          .filter((message) => message.role === "tool" && message.content)
          .map((message) => message.content);
        for (const [toolCall, result] of proposed.map((toolCall, i) => [toolCall, results[i]] as const)) {
          if (toolCall.name && result && !(toolCall.name in toolFixtures)) {
            toolFixtures[toolCall.name] = result.slice(0, 1200);
          }
        }
      }
    }

    const historical = {
      text: seed.response.text,
      tool_calls: mergedTools,
      finish_reason: seed.response.finish_reason,
      error: seed.response.error,
      usage: { ...seed.usage },
      latency_ms: seed.latency_ms,
      model: seed.model,
      call_id: seed.call_id,
      usage_note:
        "Reference behavior captured from history. It is what the current provider did, not proof of what success looks like.",
    };
    const testCase: ReplayCase = {
      case_id: "",
      workflow_id: workflowId,
      trace_id: trace.trace_id,
      origins,
      request: seed.request,
      historical,
      checks: [],
      notes: [
        "The historical response is reference behavior, not a success guarantee.",
        "Tool calls proposed by the target are captured and compared, never executed.",
      ],
    };
    const checks: Array<Record<string, unknown>> = [{ type: "no_error", required: true, source: "default" }];
    for (const name of [...new Set(mergedTools.map((call) => call.name))].sort()) {
      checks.push({ type: "tool_called", name, required: true, source: "default" });
    }
    if (historical.text.length > 40) {
      checks.push({ type: "output_min_chars", value: Math.max(20, Math.floor(historical.text.length * 0.25)), required: false, source: "default" });
    }
    if (historical.latency_ms) {
      checks.push({ type: "latency_ms_max", value: Math.max(5000, historical.latency_ms * 3), required: false, source: "default" });
    }
    const outputTokens = caseUsageValue(historical.usage as unknown as Usage, "output");
    if (outputTokens) {
      checks.push({ type: "usage_output_max", value: Math.max(256, outputTokens * 3), required: false, source: "default" });
    }
    testCase.checks = checks;
    if (Object.keys(toolFixtures).length) {
      testCase.tool_fixtures = toolFixtures;
      testCase.notes.push(
        "Recorded tool fixtures are attached (tool_fixtures). A chained replay can use them to complete the workflow offline — the harness never executes a real business tool.",
      );
    }
    return testCase;
  };

  const addCandidate = (trace: Trace, workflowId: string, origin: string) => {
    const existing = cases.find((candidate) => candidate.trace_id === trace.trace_id);
    if (existing) {
      if (!existing.origins.includes(origin)) {
        existing.origins.push(origin);
        existing.origins.sort((a, b) => ["failed", "risky", "costly", "common"].indexOf(a) - ["failed", "risky", "costly", "common"].indexOf(b));
      }
      return false;
    }
    const created = makeCase(trace, workflowId, [origin]);
    if (created) {
      cases.push(created);
      usedTraces.add(trace.trace_id);
      return true;
    }
    return false;
  };

  for (const workflow of workflowMap.workflows as Array<Record<string, any>>) {
    const members = (workflow.trace_ids as string[]).map((id) => tracesById.get(id)!).filter(Boolean);
    if (!members.length) continue;
    const picks: Array<[Trace, string]> = [];
    const failed = members.find((trace) => trace.error_count);
    if (failed) picks.push([failed, "failed"]);
    const risky = [...members].sort((a, b) => b.tool_names.length - a.tool_names.length || a.trace_id.localeCompare(b.trace_id))[0];
    if (risky) picks.push([risky, "risky"]);
    const costly = [...members]
      .sort((a, b) => (caseUsageValue(b.usage, "input") ?? 0) - (caseUsageValue(a.usage, "input") ?? 0) || a.trace_id.localeCompare(b.trace_id))[0];
    if (costly) picks.push([costly, "costly"]);
    const common = [...members].sort((a, b) => b.call_count - a.call_count || a.trace_id.localeCompare(b.trace_id))[0];
    if (common) picks.push([common, "common"]);

    let added = 0;
    for (const [trace, origin] of picks) {
      if (added >= 2) break;
      const existed = usedTraces.has(trace.trace_id);
      addCandidate(trace, workflow.id, origin);
      if (!existed) added += 1;
    }
  }

  const unassigned = (workflowMap.unassigned_trace_ids as string[]) ?? [];
  if (unassigned.length) {
    const trace = tracesById.get(unassigned[0]);
    if (trace) addCandidate(trace, "unassigned", "common");
  }

  cases.sort((a, b) => a.workflow_id.localeCompare(b.workflow_id) || a.case_id.localeCompare(b.case_id));
  let final = cases;
  if (cases.length > maxCases) {
    const failures = cases.filter((candidate) => candidate.origins.includes("failed"));
    const others = cases.filter((candidate) => !candidate.origins.includes("failed"));
    final = failures.length >= maxCases ? failures.slice(0, maxCases) : [...failures, ...others].slice(0, maxCases);
  }
  final.forEach((candidate, index) => {
    candidate.case_id = `case_${String(index + 1).padStart(3, "0")}`;
  });

  return {
    cases_version: 1,
    params: { max_cases: maxCases, per_workflow_cap: 2 },
    cases: final,
    notes: [
      "Checks are defaults generated from reference behavior. Edit cases.json to add explicit success checks where outcomes matter.",
      "Deterministic checks only. An LLM judge, if added by hand, is a fallible measurement tool, not ground truth.",
    ],
  };
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export function buildProfile(store: Store): Record<string, unknown> {
  const toolObservations = new Map<string, Array<Record<string, unknown>>>();
  const toolProposals = new Map<string, number>();
  const toolTraces = new Map<string, Set<string>>();
  const systemPrompts = new Map<string, number>();
  const providerModels = new Map<string, number>();
  const errorSamples: Array<{ trace_id: string; error: string }> = [];
  let callsWithErrors = 0;

  for (const call of store.calls) {
    const providerModel = `${call.provider}/${call.model}`;
    providerModels.set(providerModel, (providerModels.get(providerModel) ?? 0) + 1);
    if (call.response.error) {
      callsWithErrors += 1;
      if (errorSamples.length < 5) errorSamples.push({ trace_id: call.trace_id, error: call.response.error });
    }
    for (const message of call.request.messages) {
      if (message.role === "system" && message.content) {
        systemPrompts.set(message.content, (systemPrompts.get(message.content) ?? 0) + 1);
      }
    }
    for (const toolCall of call.response.tool_calls) {
      if (!toolCall.name) continue;
      toolProposals.set(toolCall.name, (toolProposals.get(toolCall.name) ?? 0) + 1);
      const observations = toolObservations.get(toolCall.name) ?? [];
      observations.push(toolCall.arguments);
      toolObservations.set(toolCall.name, observations);
    }
  }
  for (const trace of store.traces) {
    for (const name of trace.tool_names) {
      if (!toolTraces.has(name)) toolTraces.set(name, new Set());
      toolTraces.get(name)!.add(trace.trace_id);
    }
  }

  const tools = [...toolProposals.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, proposals]) => {
      const observations = toolObservations.get(name) ?? [];
      const total = observations.length || 1;
      const properties: Record<string, unknown> = {};
      const required: string[] = [];
      const keyStats = new Map<string, { types: Set<string>; count: number }>();
      for (const observation of observations) {
        for (const [key, value] of Object.entries(observation ?? {})) {
          const stat = keyStats.get(key) ?? { types: new Set<string>(), count: 0 };
          stat.count += 1;
          stat.types.add(
            typeof value === "boolean" ? "boolean"
            : typeof value === "number" ? "number"
            : typeof value === "string" ? "string"
            : Array.isArray(value) ? "array"
            : value && typeof value === "object" ? "object"
            : "unknown",
          );
          keyStats.set(key, stat);
        }
      }
      for (const [key, stat] of [...keyStats.entries()].sort()) {
        const types = new Set([...stat.types].filter((type) => type !== "unknown"));
        properties[key] = { type: types.size === 1 ? [...types][0] : types.has("string") || !types.size ? "string" : [...types].sort()[0] };
        if (stat.count >= 0.8 * total) required.push(key);
      }
      return {
        name,
        proposals,
        traces: toolTraces.get(name)?.size ?? 0,
        schema_inferred: { type: "object", properties, inferred_from: observations.length, ...(required.length ? { required } : {}) },
        example_arguments: observations.slice(0, 3).map((observation) => JSON.stringify(redactValue(observation)).slice(0, 300)),
      };
    });

  const providers = [...providerModels.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([providerModel, calls]) => {
      const [provider, ...rest] = providerModel.split("/");
      return { provider, model: rest.join("/") || "unknown", calls };
    });

  const sessionOrigins: Record<string, number> = {};
  const namedSessions: Array<Record<string, unknown>> = [];
  for (const trace of store.traces) {
    if (trace.origin) sessionOrigins[trace.origin] = (sessionOrigins[trace.origin] ?? 0) + 1;
    if (trace.title) {
      namedSessions.push({ trace_id: trace.trace_id, title: excerpt(trace.title, 120), origin: trace.origin, calls: trace.call_count });
    }
  }

  return {
    profile: {
      profile_version: 1,
      totals: {
        traces: store.totals.traces,
        calls: store.totals.calls,
        sessions: store.sessions.length,
        calls_with_errors: callsWithErrors,
      },
      usage: store.totals.usage,
      providers,
      session_origins: sessionOrigins,
      named_sessions: namedSessions.slice(0, 8),
      system_prompts: [...systemPrompts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([prompt, count]) => ({ excerpt: excerpt(prompt, 600), calls_using: count })),
      tools,
      error_samples: errorSamples,
      notes: [
        "Tool schemas are inferred from observed arguments; a reviewer should confirm them before reuse.",
        "All content was redacted at import; excerpts are capped.",
      ],
    },
  };
}
