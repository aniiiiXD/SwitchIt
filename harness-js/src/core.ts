/**
 * SwitchIt! harness core — canonical schema, redaction, usage provenance,
 * normalization, and the session store. TypeScript port of switchit/core.py.
 *
 * Boundaries kept honest throughout:
 * - Workflow labels and similarity scores are triage aids, not measured accuracy.
 * - Proposed tool calls are captured, never executed.
 * - A historical model response is reference behavior, not proof of success.
 * - Token provenance stays separated: recorded, estimated, unknown.
 */

export type UsageBasis = "recorded" | "estimated" | "unknown";

export interface UsageBucket {
  calls: number;
  input_tokens_known: number;
  output_tokens_known: number;
  input_missing_calls: number;
  output_missing_calls: number;
  calls_missing_either: number;
}

export type Usage = Record<UsageBasis, UsageBucket>;

export const USAGE_BASES: UsageBasis[] = ["recorded", "estimated", "unknown"];

export function emptyUsage(): Usage {
  const bucket = (): UsageBucket => ({
    calls: 0,
    input_tokens_known: 0,
    output_tokens_known: 0,
    input_missing_calls: 0,
    output_missing_calls: 0,
    calls_missing_either: 0,
  });
  return { recorded: bucket(), estimated: bucket(), unknown: bucket() };
}

export function normalizeBasis(basis?: string | null): UsageBasis {
  const value = (basis ?? "unknown").trim().toLowerCase();
  if (value.startsWith("source_reported") || ["recorded", "source"].includes(value)) return "recorded";
  if (value.startsWith("estimated")) return "estimated";
  return "unknown";
}

export function addUsage(
  usage: Usage,
  inputTokens: number | null,
  outputTokens: number | null,
  basis: string,
): void {
  const key = normalizeBasis(basis);
  const bucket = usage[key];
  bucket.calls += 1;
  for (const [suffix, value] of [
    ["input", inputTokens],
    ["output", outputTokens],
  ] as const) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
      bucket[`${suffix}_missing_calls`] += 1;
    } else {
      bucket[`${suffix}_tokens_known`] += Math.trunc(Number(value));
    }
  }
  if (inputTokens === null || inputTokens === undefined || outputTokens === null || outputTokens === undefined) {
    bucket.calls_missing_either += 1;
  }
}

export function mergeUsage(...usages: Usage[]): Usage {
  const merged = emptyUsage();
  for (const usage of usages) {
    for (const basis of USAGE_BASES) {
      const source = usage[basis];
      const target = merged[basis];
      for (const key of Object.keys(target) as Array<keyof UsageBucket>) {
        target[key] += source?.[key] ?? 0;
      }
    }
  }
  return merged;
}

// ---------------------------------------------------------------------------
// Redaction — applied at import; redacted content is the only content stored.
// ---------------------------------------------------------------------------

const SECRET_LABEL = /(password|passwd|api[ _-]?key|apikey|secret|token|bearer|authorization)(\s*[:=]\s*|\s+)([^\s,;"']+)/gi;
const EMAIL = /\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g;
const AWS_KEY = /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g;
const SK_KEY = /\bsk-[A-Za-z0-9_-]{16,}\b/g;
const PHONE = /(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)/g;
const WHITESPACE = /\s+/g;

export function redactText(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = String(value).replace(WHITESPACE, " ").trim();
  text = text.replace(SECRET_LABEL, (_m, label: string, sep: string) => `${label}${sep}[REDACTED]`);
  text = text.replace(AWS_KEY, "[REDACTED_KEY]");
  text = text.replace(SK_KEY, "[REDACTED_KEY]");
  text = text.replace(EMAIL, "[EMAIL]");
  text = text.replace(PHONE, "[NUMBER]");
  return text;
}

export function excerpt(text: string, limit = 8000): string {
  const redacted = redactText(text);
  if (redacted.length <= limit) return redacted;
  const head = Math.max(1, Math.floor(limit * 0.75));
  const tail = Math.max(1, limit - head - 5);
  return `${redacted.slice(0, head)} … ${redacted.slice(-tail)}`;
}

export function redactValue<T>(value: T): T {
  if (typeof value === "string") return excerpt(String(value)) as unknown as T;
  if (Array.isArray(value)) return value.map((item) => redactValue(item)) as unknown as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      out[key] = redactValue(item);
    }
    return out as unknown as T;
  }
  return value;
}

export interface Message {
  role: string;
  content: string;
  name?: string;
  tool_call_id?: string;
  tool_calls?: Array<{ name: string; arguments: Record<string, unknown> }>;
}

export function redactMessages(messages: unknown): Message[] {
  if (!Array.isArray(messages)) return [];
  return messages.map((message): Message => {
    if (!message || typeof message !== "object") {
      return { role: "user", content: excerpt(String(message)) };
    }
    const record = message as Record<string, unknown>;
    const entry: Message = {
      role: String(record.role ?? "user"),
      content: excerpt(record.content as string ?? ""),
    };
    for (const key of ["name", "tool_call_id"] as const) {
      if (record[key]) entry[key] = redactText(String(record[key])).slice(0, 200);
    }
    if (Array.isArray(record.tool_calls)) {
      entry.tool_calls = (record.tool_calls as unknown[]).map((call) =>
        normalizeToolCall(call),
      );
    }
    return entry;
  });
}

export interface ToolSchema {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export function redactTools(tools: unknown): ToolSchema[] {
  if (!Array.isArray(tools)) return [];
  const clean: ToolSchema[] = [];
  for (const tool of tools) {
    if (typeof tool === "string") {
      const name = redactText(tool).slice(0, 200);
      if (name) clean.push({ name, description: "", parameters: {} });
      continue;
    }
    if (!tool || typeof tool !== "object") continue;
    const record = tool as Record<string, unknown>;
    const fn = record.function as Record<string, unknown> | undefined;
    const name = redactText(String((fn?.name as string) ?? record.name ?? "")).slice(0, 200);
    if (!name) continue;
    clean.push({
      name,
      description: excerpt(String(fn?.description ?? record.description ?? ""), 400),
      parameters: redactValue(
        (fn?.parameters as Record<string, unknown>) ?? (record.parameters as Record<string, unknown>) ?? {},
      ) as Record<string, unknown>,
    });
  }
  return clean;
}

// ---------------------------------------------------------------------------
// Canonical calls, traces, sessions
// ---------------------------------------------------------------------------

export interface CanonicalResponse {
  text: string;
  tool_calls: Array<{ name: string; arguments: Record<string, unknown> }>;
  finish_reason: string | null;
  error: string | null;
}

export interface CanonicalCall {
  call_id: string;
  trace_id: string;
  session_id: string | null;
  span_id: string | null;
  call_index: number;
  provider: string;
  model: string;
  request: { messages: Message[]; tools: ToolSchema[] };
  response: CanonicalResponse;
  latency_ms: number | null;
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    basis: UsageBasis;
    estimator: string | null;
  };
  source: { importer: string; location: string };
}

export interface Trace {
  trace_id: string;
  session_id: string | null;
  call_ids: string[];
  call_count: number;
  error_count: number;
  tool_names: string[];
  user_requests: string[];
  latency_ms_max: number | null;
  usage: Usage;
  title?: string;
  origin?: string;
  cwd?: string;
}

export function normalizeToolCall(raw: unknown): { name: string; arguments: Record<string, unknown> } {
  if (!raw || typeof raw !== "object") {
    return { name: redactText(String(raw)).slice(0, 200), arguments: {} };
  }
  const record = raw as Record<string, unknown>;
  const fn = record.function as Record<string, unknown> | undefined;
  const name = redactText(String(record.name ?? fn?.name ?? "")).slice(0, 200);
  let args = record.arguments ?? record.args ?? fn?.arguments ?? {};
  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch {
      args = { _raw: String(args).slice(0, 400) };
    }
  }
  if (!args || typeof args !== "object") args = {};
  return { name, arguments: redactValue(args) as Record<string, unknown> };
}

function contentToText(content: unknown): string {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object") {
          const record = block as Record<string, unknown>;
          if (typeof record.text === "string") return record.text;
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

export function normalizeResponse(response: unknown): CanonicalResponse {
  const result: CanonicalResponse = { text: "", tool_calls: [], finish_reason: null, error: null };
  if (response === null || response === undefined) return result;
  if (typeof response === "string") {
    result.text = excerpt(response);
    return result;
  }
  if (typeof response !== "object") {
    result.text = excerpt(String(response));
    return result;
  }
  const record = response as Record<string, any>;

  const absorb = (message: Record<string, unknown>) => {
    const role = String(message.role ?? "");
    if (role && role !== "assistant" && !message.tool_calls) return;
    const text = contentToText(message.content);
    if (text) result.text = excerpt(`${result.text} ${text}`.trim());
    for (const call of (message.tool_calls as unknown[]) ?? []) {
      if (call) result.tool_calls.push(normalizeToolCall(call));
    }
  };

  if (Array.isArray(record.messages)) {
    for (const message of record.messages) {
      if (message && typeof message === "object") absorb(message as Record<string, unknown>);
    }
  }
  if (record.message && typeof record.message === "object") {
    absorb(record.message as Record<string, any>);
    if (result.finish_reason === null) {
      result.finish_reason = (record.message.finish_reason ?? record.message.stop_reason) as string | null;
    }
    if (result.error === null && (record.message as Record<string, unknown>).error) {
      result.error = redactText(String((record.message as Record<string, unknown>).error)).slice(0, 400);
    }
  }
  if (result.tool_calls.length === 0 && Array.isArray(record.tool_calls)) {
    for (const call of record.tool_calls) {
      if (call) result.tool_calls.push(normalizeToolCall(call));
    }
  }
  if (!result.text) {
    const content = record.content ?? record.text ?? record.output ?? "";
    if (Array.isArray(content)) {
      const parts: string[] = [];
      for (const block of content) {
        if (typeof block === "string") parts.push(block);
        else if (block && typeof block === "object") {
          const record2 = block as Record<string, unknown>;
          if (typeof record2.text === "string") parts.push(record2.text);
          if (record2.type === "tool_use" || record2.type === "function_call" || record2.name) {
            result.tool_calls.push(normalizeToolCall(block));
          }
        }
      }
      result.text = excerpt(parts.filter(Boolean).join(" "));
    } else if (content !== null && content !== undefined) {
      result.text = excerpt(String(content));
    }
  }
  if (result.finish_reason === null) {
    result.finish_reason = (record.finish_reason ?? record.stop_reason) as string | null;
  }
  if (result.error === null && record.error !== null && record.error !== undefined) {
    result.error = redactText(String(record.error)).slice(0, 400) || null;
  }
  return result;
}

export function normalizeRequest(request: unknown): { messages: Message[]; tools: ToolSchema[] } {
  let messages: unknown = [];
  let tools: unknown = [];
  if (typeof request === "string") {
    try {
      return normalizeRequest(JSON.parse(request));
    } catch {
      messages = [{ role: "user", content: request }];
    }
  } else if (Array.isArray(request)) {
    messages = request;
  } else if (request && typeof request === "object") {
    const record = request as Record<string, unknown>;
    rawMessages: {
      let raw = record.messages ?? record.input_messages;
      if (typeof raw === "string") {
        try {
          raw = JSON.parse(raw);
        } catch {
          raw = [{ role: "user", content: raw }];
        }
      }
      if (raw === undefined || raw === null) {
        const prompt = record.prompt;
        if (typeof prompt === "string") {
          messages = [{ role: "user", content: prompt }];
          break rawMessages;
        }
        messages = Array.isArray(prompt) ? prompt : [];
      } else {
        messages = raw;
      }
    }
    tools = record.tools ?? record.tool_definitions ?? [];
  }
  return { messages: redactMessages(messages), tools: redactTools(tools) };
}

function stableId(...parts: unknown[]): string {
  return Buffer.from(parts.map(String).join("\x1f"), "utf8").toString("hex").slice(0, 24);
}

export interface RawCall {
  trace_id?: string | null;
  traceId?: string | null;
  conversation_id?: string | null;
  id?: string | null;
  observation_id?: string | null;
  index?: number | null;
  call_id?: string | null;
  session_id?: string | null;
  span_id?: string | null;
  call_index?: number | null;
  provider?: string | null;
  model?: string | null;
  request?: unknown;
  response?: unknown;
  latency_ms?: number | null;
  error?: unknown;
  input_tokens?: number | null;
  output_tokens?: number | null;
  usage?: unknown;
  usage_basis?: string | null;
  usage_estimator?: string | null;
}

export function makeCall(raw: RawCall, importer: string, location: string): CanonicalCall {
  const traceId = raw.trace_id ?? raw.traceId ?? raw.conversation_id;
  if (!traceId) throw new Error(`${location}: record has no trace_id`);
  const callId = raw.call_id ?? raw.id ?? raw.span_id ?? raw.observation_id;
  let callIndex = raw.call_index ?? raw.index ?? 0;
  const usage = (raw.usage ?? {}) as Record<string, unknown>;
  const inputFromUsage = usage.input_tokens as number | undefined;
  const outputFromUsage = usage.output_tokens as number | undefined;
  const inputTokens = raw.input_tokens ?? inputFromUsage ?? null;
  const outputTokens = raw.output_tokens ?? outputFromUsage ?? null;
  let basis = (raw.usage_basis ?? usage.basis) as string | undefined;
  if (!basis && (inputTokens !== null || outputTokens !== null)) basis = "recorded";
  if (!basis) basis = "unknown";
  const response = normalizeResponse(raw.response);
  if (raw.error !== null && raw.error !== undefined) {
    response.error = redactText(typeof raw.error === "string" ? raw.error : JSON.stringify(raw.error)).slice(0, 400) || response.error;
  }
  return {
    call_id: String(callId ?? stableId("call", importer, location, traceId, callIndex)),
    trace_id: String(traceId),
    session_id: raw.session_id ? String(raw.session_id) : null,
    span_id: raw.span_id ? String(raw.span_id) : null,
    call_index: Number(callIndex) || 0,
    provider: redactText(String(raw.provider ?? "unknown")).slice(0, 120),
    model: redactText(String(raw.model ?? "unknown")).slice(0, 160),
    request: normalizeRequest(raw.request),
    response,
    latency_ms: raw.latency_ms ?? null,
    usage: {
      input_tokens: inputTokens === null ? null : Math.trunc(Number(inputTokens)) || null,
      output_tokens: outputTokens === null ? null : Math.trunc(Number(outputTokens)) || null,
      basis: normalizeBasis(basis),
      estimator: raw.usage_estimator ?? null,
    },
    source: { importer, location },
  };
}

export function buildTraces(calls: CanonicalCall[], sessionMeta?: Record<string, Record<string, unknown>>): Trace[] {
  const grouped = new Map<string, CanonicalCall[]>();
  for (const call of calls) {
    const list = grouped.get(call.trace_id) ?? [];
    list.push(call);
    grouped.set(call.trace_id, list);
  }
  const traces: Trace[] = [];
  for (const traceId of [...grouped.keys()].sort()) {
    const members = (grouped.get(traceId) ?? []).sort(
      (a, b) => a.call_index - b.call_index || a.call_id.localeCompare(b.call_id),
    );
    const usage = emptyUsage();
    let errorCount = 0;
    const toolNames: string[] = [];
    const userRequests: string[] = [];
    const seenRequests = new Set<string>();
    const latencies: number[] = [];
    let sessionId: string | null = null;

    for (const call of members) {
      addUsage(usage, call.usage.input_tokens, call.usage.output_tokens, call.usage.basis);
      if (call.response.error) errorCount += 1;
      for (const call_ of call.response.tool_calls) {
        if (call_.name && !toolNames.includes(call_.name)) toolNames.push(call_.name);
      }
      for (const tool of call.request.tools) {
        if (tool.name && !toolNames.includes(tool.name)) toolNames.push(tool.name);
      }
      for (const message of call.request.messages) {
        if (message.role === "user" && message.content) {
          const key = message.content.slice(0, 200).toLowerCase();
          if (!seenRequests.has(key)) {
            seenRequests.add(key);
            userRequests.push(message.content);
          }
        }
      }
      if (call.latency_ms !== null) latencies.push(call.latency_ms);
      if (call.session_id) sessionId = call.session_id;
    }

    const trace: Trace = {
      trace_id: traceId,
      session_id: sessionId,
      call_ids: members.map((call) => call.call_id),
      call_count: members.length,
      error_count: errorCount,
      tool_names: toolNames.sort(),
      user_requests: userRequests.slice(0, 4),
      latency_ms_max: latencies.length ? Math.max(...latencies) : null,
      usage,
    };
    const meta = sessionMeta?.[traceId];
    if (meta) {
      const writable = trace as unknown as Record<string, unknown>;
      for (const key of ["title", "origin", "cwd"] as const) {
        if (meta[key]) writable[key] = meta[key];
      }
    }
    traces.push(trace);
  }
  return traces;
}

export function buildSessions(traces: Trace[]): Array<{ session_id: string; trace_ids: string[]; call_count: number }> {
  const sessions = new Map<string, { session_id: string; trace_ids: string[]; call_count: number }>();
  for (const trace of traces) {
    if (!trace.session_id) continue;
    const session = sessions.get(trace.session_id) ?? {
      session_id: trace.session_id,
      trace_ids: [],
      call_count: 0,
    };
    session.trace_ids.push(trace.trace_id);
    session.call_count += trace.call_count;
    sessions.set(trace.session_id, session);
  }
  return [...sessions.values()].sort((a, b) => a.session_id.localeCompare(b.session_id));
}

export interface Store {
  store_version: number;
  app_config: unknown;
  sessions: ReturnType<typeof buildSessions>;
  traces: Trace[];
  calls: CanonicalCall[];
  totals: { traces: number; calls: number; usage: Usage };
  source_files: Array<{ importer: string; path: string; records: number }>;
  notes: string[];
}

export function buildStore(
  calls: CanonicalCall[],
  options: {
    appConfig?: unknown;
    sourceFiles?: Array<{ importer: string; path: string; records: number }>;
    notes?: string[];
    sessionMeta?: Record<string, Record<string, unknown>>;
  } = {},
): Store {
  const traces = buildTraces(calls, options.sessionMeta);
  return {
    store_version: 1,
    app_config: options.appConfig ? redactValue(options.appConfig) : null,
    sessions: buildSessions(traces),
    traces,
    calls,
    totals: {
      traces: traces.length,
      calls: calls.length,
      usage: mergeUsage(...traces.map((trace) => trace.usage)),
    },
    source_files: options.sourceFiles ?? [],
    notes: [
      "All message content was redacted at import; redacted content is the only content stored, replayed, or reported.",
      ...(options.notes ?? []),
    ],
  };
}
