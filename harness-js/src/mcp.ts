#!/usr/bin/env node
/**
 * SwitchIt! MCP server (stdio, JSON-RPC 2.0) — exposes the pipeline as tools
 * for any MCP-capable agent. Port of switchit/mcp_server.py (raw protocol,
 * zero dependencies).
 */

import process from "node:process";
import { spawn } from "node:child_process";

const TOOLS = [
  { name: "switchit_import", description: "Import trace exports and coding-agent sessions into the canonical store (demo=true for the bundled synthetic dataset).", inputSchema: { type: "object", properties: { state_dir: { type: "string" }, jsonl: { type: "array", items: { type: "string" } }, agents: { type: "boolean" }, demo: { type: "boolean" } } } },
  { name: "switchit_profile", description: "Dig through the imported sessions: system prompts, tool inventory with inferred schemas, provider mix, error samples.", inputSchema: { type: "object", properties: { state_dir: { type: "string" } } } },
  { name: "switchit_map", description: "Group traces into provisional workflows with evidence and uncertainty flags.", inputSchema: { type: "object", properties: { state_dir: { type: "string" }, threshold: { type: "number" } } } },
  { name: "switchit_cases", description: "Select representative replay cases with default success checks.", inputSchema: { type: "object", properties: { state_dir: { type: "string" }, max_cases: { type: "number" } } } },
  { name: "switchit_replay", description: "Replay frozen cases through the recorded baseline and a target adapter. Offline with target=mock; any live target requires remote=true and env keys.", inputSchema: { type: "object", properties: { state_dir: { type: "string" }, target: { type: "string", enum: ["mock", "openai", "anthropic", "grok", "groq", "deepseek", "mistral", "openrouter", "together", "fireworks", "ollama", "vllm"] }, remote: { type: "boolean" }, target_model: { type: "string" }, chain: { type: "boolean" } } } },
  { name: "switchit_report", description: "Compare replay results and write the JSON + HTML migration report.", inputSchema: { type: "object", properties: { state_dir: { type: "string" } } } },
];

function toolCall(name, args, respond) {
  const stateDir = args.state_dir ?? ".switchit";
  const base = [name.replace("switchit_", ""), "--state-dir", stateDir];
  const extra: string[] = [];
  if (name === "switchit_import" || name === "switchit_run") {
    if (args.demo) extra.push("--demo");
    if (args.agents) extra.push("--agents");
    for (const filePath of args.jsonl ?? []) extra.push("--jsonl", filePath);
  }
  if (name === "switchit_map" && args.threshold != null) extra.push("--threshold", String(args.threshold));
  if (name === "switchit_cases" && args.max_cases != null) extra.push("--max-cases", String(args.max_cases));
  if (name === "switchit_replay") {
    if (args.target) extra.push("--target", args.target);
    if (args.remote) extra.push("--remote");
    if (args.target_model) extra.push("--target-model", args.target_model);
    if (args.chain) extra.push("--chain");
  }
  const child = spawn(process.execPath, [new URL("./cli.js", import.meta.url).pathname, ...base, ...extra], {
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => (output += chunk));
  let errorText = "";
  child.stderr.on("data", (chunk) => (errorText += chunk));
  child.on("close", (code) => {
    if (code === 0) respond({ content: [{ type: "text", text: output.trim() || "done" }], isError: false });
    else respond({ content: [{ type: "text", text: `exit ${code}\n${output}\n${errorText}`.trim() }], isError: true });
  });
  child.on("error", (error) => respond({ content: [{ type: "text", text: error.message }], isError: true }));
}

function handleMessage(message: Record<string, any>, respond: (response: Record<string, any> | null) => void): void {
  const method = message.method;
  const id = message.id;
  if (method === "initialize") {
    respond({
      jsonrpc: "2.0", id,
      result: {
        protocolVersion: message.params?.protocolVersion ?? "2025-06-18",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "switchit", version: "0.1.0" },
      },
    });
    return;
  }
  if (typeof method === "string" && method.startsWith("notifications/")) return;
  if (method === "ping") { respond({ jsonrpc: "2.0", id, result: {} }); return; }
  if (method === "tools/list") { respond({ jsonrpc: "2.0", id, result: { tools: TOOLS } }); return; }
  if (method === "tools/call") {
    const name = message.params?.name;
    const args = message.params?.arguments ?? {};
    const known = TOOLS.some((tool) => tool.name === name);
    if (!known) {
      respond({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text: `Unknown tool ${name}.` }], isError: true } });
      return;
    }
    toolCall(name, args, (result_) => respond({ jsonrpc: "2.0", id, result: result_ }));
    return;
  }
  if (id === undefined) return;
  respond({ jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } });
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let newlineIndex: number;
  while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, newlineIndex).trim();
    buffer = buffer.slice(newlineIndex + 1);
    if (!line) continue;
    try {
      handleMessage(JSON.parse(line), (response) => {
        if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
      });
    } catch {
      console.error("switchit-mcp: ignoring non-JSON line");
    }
  }
});
process.stdin.on("end", () => process.exit(0));
console.error(`switchit-mcp 0.1.0: serving ${TOOLS.length} tools on stdio`);
