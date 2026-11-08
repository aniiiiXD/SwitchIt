/**
 * Importers: JSON/JSONL exports plus local coding-agent session formats
 * (Claude Code, Codex rollouts). Node port of the Python importers.
 * Parsing is tolerant: unknown shapes are skipped, never fatal.
 */

import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import type { RawCall } from "./core.js";

export async function readJsonLines(filePath: string): Promise<unknown[]> {
  const records: unknown[] = [];
  const stream = fs.createReadStream(filePath, { encoding: "utf8" });
  const lines = readline.createInterface({ input: stream, crlfDelay: Infinity });
  for await (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      records.push(JSON.parse(trimmed));
    } catch {
      // skip malformed lines; tolerant by design
    }
  }
  return records;
}

/** Generic JSON/JSONL records (the documented flexible shape). */
export function parseJsonlRecord(record: Record<string, unknown>, location: string): RawCall {
  if (!record || typeof record !== "object") {
    throw new Error(`${location}: record is not an object`);
  }
  const get = (...keys: string[]) => {
    for (const key of keys) {
      if (record[key] !== undefined && record[key] !== null) return record[key];
    }
    return undefined;
  };
  let request = record.request ?? record.input;
  if (request === undefined) {
    const messages = get("messages", "input_messages");
    if (messages !== undefined) request = { messages, tools: get("tools") };
    else {
      const prompt = get("prompt");
      request = typeof prompt === "string" ? { prompt } : (prompt ?? []);
    }
  }
  let response = record.response ?? record.output;
  if (response === undefined) {
    const outputMessages = get("output_messages");
    if (outputMessages !== undefined) response = { messages: outputMessages };
    else if (get("response_text", "output_text") !== undefined || record.tool_calls) {
      response = {
        text: get("response_text", "output_text"),
        tool_calls: record.tool_calls ?? [],
        finish_reason: record.finish_reason,
        error: record.error,
      };
    }
  }
  return {
    trace_id: get("trace_id", "traceId", "conversation_id") as string | null,
    call_id: get("call_id", "id") as string | null,
    session_id: get("session_id", "sessionId", "session") as string | null,
    span_id: get("span_id", "spanId") as string | null,
    call_index: (get("call_index", "index") as number | null) ?? null,
    provider: get("provider") as string | null,
    model: get("model") as string | null,
    request,
    response,
    latency_ms: (record.latency_ms as number | null) ?? null,
    error: record.error ?? null,
    input_tokens: (record.input_tokens as number | null) ?? null,
    output_tokens: (record.output_tokens as number | null) ?? null,
    usage: record.usage ?? null,
    usage_basis: (record.usage_basis as string | null) ?? null,
    usage_estimator: (record.usage_estimator ?? record.estimator) as string | null,
  };
}

export async function importJsonl(filePath: string): Promise<RawCall[]> {
  const records = await readJsonLines(filePath);
  const rawCalls: RawCall[] = [];
  for (const [index, record] of records.entries()) {
    if (!record || typeof record !== "object") continue;
    if (Array.isArray(record)) {
      record.forEach((item, sub) => {
        if (item && typeof item === "object") {
          rawCalls.push(parseJsonlRecord(item as Record<string, unknown>, `${filePath}:${index + 1}.${sub}`));
        }
      });
    } else {
      rawCalls.push(parseJsonlRecord(record as Record<string, unknown>, `${filePath}:${index + 1}`));
    }
  }
  return rawCalls;
}

// ---------------------------------------------------------------------------
// Claude Code: ~/.claude/projects/**/*.jsonl
// ---------------------------------------------------------------------------

export async function importClaudeCode(filePath: string): Promise<RawCall[]> {
  const rawCalls: RawCall[] = [];
  let sessionId = path.basename(filePath, ".jsonl");
  let pendingTools: string[] = [];
  let lastTime: number | null = null;

  for await (const record of await readJsonLines(filePath)) {
    const record_ = record as Record<string, any>;
    const type = record_?.type;
    if (type !== "user" && type !== "assistant") continue;
    if (record_.sessionId) sessionId = String(record_.sessionId);
    const message = record_.message ?? {};
    const role = String(message.role ?? type);
    const timestamp = record_.timestamp ? Date.parse(record_.timestamp) / 1000 : null;

    if (role === "assistant") {
      const blocks = Array.isArray(message.content) ? message.content : [];
      const text = blocks
        .filter((block: any) => block?.type === "text" && typeof block.text === "string")
        .map((block: any) => block.text)
        .join("\n");
      const toolCalls = blocks
        .filter((block: any) => block?.type === "tool_use")
        .map((block: any) => ({ name: String(block.name ?? ""), arguments: block.input ?? {} }))
        .filter((call: any) => call.name);
      pendingTools = toolCalls.map((call: any) => call.name);
      rawCalls.push({
        trace_id: `claude-code:${sessionId}`,
        call_id: message.id ?? null,
        session_id: sessionId,
        call_index: rawCalls.length,
        provider: "anthropic",
        model: message.model ?? null,
        request: { messages: [] },
        response: { text, tool_calls: toolCalls, finish_reason: message.stop_reason ?? null },
        latency_ms: timestamp && lastTime && timestamp > lastTime ? Math.round((timestamp - lastTime) * 1000) : null,
        usage: message.usage
          ? { input_tokens: message.usage.input_tokens ?? null, output_tokens: message.usage.output_tokens ?? null, basis: "recorded" }
          : null,
      });
      if (timestamp) lastTime = timestamp;
    } else if (role === "user") {
      const blocks = message.content;
      if (Array.isArray(blocks)) {
        for (const block of blocks) {
          if (block?.type === "tool_result") {
            const resultText =
              typeof block.content === "string"
                ? block.content
                : Array.isArray(block.content)
                  ? block.content.map((part: any) => part?.text ?? "").join("\n")
                  : "";
            if (resultText) {
              rawCalls.push({
                trace_id: `claude-code:${sessionId}`,
                session_id: sessionId,
                call_index: rawCalls.length,
                provider: "anthropic",
                model: "tool-result",
                request: {
                  messages: [{ role: "tool", content: resultText, name: pendingTools.shift() ?? "unknown_tool" }],
                },
                response: null,
              });
            }
          }
        }
      } else if (typeof blocks === "string" && blocks.trim()) {
        rawCalls.push({
          trace_id: `claude-code:${sessionId}`,
          session_id: sessionId,
          call_index: rawCalls.length,
          provider: "anthropic",
          model: "user-turn",
          request: { messages: [{ role: "user", content: blocks }] },
          response: null,
        });
      }
    }
  }
  return rawCalls;
}

// ---------------------------------------------------------------------------
// Codex: ~/.codex/sessions/**/*.jsonl (plain; .zst handled when zstd available)
// ---------------------------------------------------------------------------

export async function importCodex(filePath: string): Promise<RawCall[]> {
  const rawCalls: RawCall[] = [];
  let sessionId: string | null = null;
  let systemText = "";
  let pendingUsage: { input_tokens: number | null; output_tokens: number | null; basis: string } | null = null;
  let index = 0;
  let systemAttached = false;

  for await (const record of await readJsonLines(filePath)) {
    const record_ = record as Record<string, any>;
    const type = record_?.type;
    const payload = record_?.payload ?? {};

    if (type === "session_meta") {
      sessionId = payload.session_id ?? payload.id ?? path.basename(filePath, ".jsonl");
      const instructions = payload.base_instructions;
      systemText =
        typeof instructions === "string"
          ? instructions
          : typeof instructions?.text === "string"
            ? instructions.text
            : "";
      continue;
    }
    if (type === "event_msg") {
      const usageInfo = payload.info?.total_token_usage ?? payload.info?.token_usage;
      if (usageInfo && (usageInfo.input_tokens != null || usageInfo.output_tokens != null)) {
        pendingUsage = { input_tokens: usageInfo.input_tokens ?? null, output_tokens: usageInfo.output_tokens ?? null, basis: "recorded" };
      }
      continue;
    }
    if (type !== "response_item") continue;

    const payloadType = payload.type;
    const traceId = `codex:${sessionId ?? path.basename(filePath, ".jsonl")}`;
    if (payloadType === "message") {
      const role = payload.role ?? "user";
      const text = (payload.content ?? [])
        .map((part: any) => part?.text ?? "")
        .filter(Boolean)
        .join("\n");
      if (!text) continue;
      if (role === "assistant") {
        rawCalls.push({
          trace_id: traceId,
          session_id: sessionId,
          call_index: index,
          provider: "openai",
          request: {
            messages: systemText && !systemAttached ? [{ role: "system", content: systemText }] : [],
          },
          response: { text, tool_calls: [], finish_reason: "stop" },
          usage: pendingUsage,
        });
        systemAttached = true;
        index += 1;
        pendingUsage = null;
      } else if (role === "user") {
        rawCalls.push({
          trace_id: traceId,
          session_id: sessionId,
          call_index: index,
          provider: "openai",
          model: "user-turn",
          request: { messages: [{ role: "user", content: text }] },
          response: null,
        });
        index += 1;
      }
    } else if (payloadType === "function_call") {
      rawCalls.push({
        trace_id: traceId,
        session_id: sessionId,
        call_index: index,
        provider: "openai",
        model: "tool-call",
        request: { messages: [] },
        response: {
          text: "",
          finish_reason: "tool_calls",
          tool_calls: [{ name: payload.name ?? "", arguments: safeJson(payload.arguments) }],
        },
      });
      index += 1;
    } else if (payloadType === "function_call_output") {
      rawCalls.push({
        trace_id: traceId,
        session_id: sessionId,
        call_index: index,
        provider: "openai",
        model: "tool-result",
        request: { messages: [{ role: "tool", content: String(payload.output ?? ""), name: "unknown_tool" }] },
        response: null,
      });
      index += 1;
    }
  }
  return rawCalls;
}

function safeJson(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object") return value as Record<string, unknown>;
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" ? parsed : { _raw: value.slice(0, 400) };
    } catch {
      return { _raw: value.slice(0, 400) };
    }
  }
  return {};
}
