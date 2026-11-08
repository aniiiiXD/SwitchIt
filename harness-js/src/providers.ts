/**
 * Provider adapters: recorded baseline, mock, OpenAI-compatible (with
 * presets), and Anthropic. Live adapters are inert without the explicit
 * remote flag; keys come from the environment only.
 */

export interface ReplayResult {
  text: string;
  tool_calls: Array<{ name: string; arguments: Record<string, unknown> }>;
  finish_reason: string | null;
  error: string | null;
  latency_ms: number;
  usage: { input_tokens: number | null; output_tokens: number | null; basis: string; estimator: string | null };
  adapter: string;
  model: string | null;
}

export interface ReplayRequest {
  messages: Array<{ role: string; content: string; tool_calls?: Array<{ name: string; arguments: unknown }>; tool_call_id?: string; name?: string }>;
  tools: Array<{ name: string }>;
}

export interface CaseLike {
  request: { messages: Array<{ role: string; content: string }>; tools: Array<{ name: string }> };
  historical: {
    text: string;
    tool_calls: Array<{ name: string; arguments: Record<string, unknown> }>;
    finish_reason: string | null;
    error: string | null;
    usage: { input_tokens: number | null; output_tokens: number | null; basis: string; estimator: string | null };
    latency_ms: number | null;
    model: string | null;
  };
}

export class ProviderError extends Error {}
export class RemoteDisabledError extends ProviderError {}

function result(
  adapter: string,
  partial: Omit<ReplayResult, "adapter">,
): ReplayResult {
  return { ...partial, adapter };
}

function unknownUsage() {
  return { input_tokens: null, output_tokens: null, basis: "unknown", estimator: null };
}

function estimateUsage(text: string) {
  const words = (text.match(/\S+/g) ?? []).length;
  return { input_tokens: null, output_tokens: Math.max(1, Math.floor(words * 1.3)), basis: "estimated", estimator: "word_count" };
}

// ---------------------------------------------------------------------------
// Recorded baseline
// ---------------------------------------------------------------------------

export class RecordedBaselineAdapter {
  name = "recorded";
  kind = "offline";
  remote = false;

  capabilities() {
    return {
      tool_calling: true,
      structured_output: true,
      streaming: false,
      caching: false,
      max_context_tokens: null,
      notes: "Recorded historical responses; capabilities mirror whatever the source system used.",
    };
  }

  replay(_request: ReplayRequest, testCase?: CaseLike): ReplayResult {
    if (!testCase?.historical) {
      throw new ProviderError("The recorded adapter replays case history and requires a case with a 'historical' field.");
    }
    const historical = testCase.historical;
    return result(this.name, {
      text: historical.text ?? "",
      tool_calls: historical.tool_calls ?? [],
      finish_reason: historical.finish_reason,
      error: historical.error,
      latency_ms: historical.latency_ms ?? 0,
      usage: { ...historical.usage, basis: historical.usage?.basis ?? "unknown", estimator: historical.usage?.estimator ?? null },
      model: historical.model,
    });
  }
}

// ---------------------------------------------------------------------------
// Mock
// ---------------------------------------------------------------------------

export type MockMode = "mirror" | "echo" | "canned" | "degrade" | "fail";

export class MockAdapter {
  name = "mock";
  kind = "offline";
  remote = false;
  model: string;

  constructor(private mode: MockMode = "mirror", private text: string | null = null, private toolCalls: Array<{ name: string; arguments: Record<string, unknown> }> = [], model = "mock-model") {
    if (!["mirror", "echo", "canned", "degrade", "fail"].includes(mode)) {
      throw new ProviderError(`Unknown mock mode ${mode}`);
    }
    this.model = model;
  }

  capabilities() {
    return {
      tool_calling: ["mirror", "canned"].includes(this.mode),
      structured_output: ["mirror", "canned"].includes(this.mode),
      streaming: false,
      caching: false,
      max_context_tokens: 128000,
      notes: `Deterministic mock adapter in '${this.mode}' mode, for offline runs only.`,
    };
  }

  replay(request: ReplayRequest, testCase?: CaseLike): ReplayResult {
    const started = Date.now();
    let error: string | null = null;
    let text = "";
    let toolCalls: Array<{ name: string; arguments: Record<string, unknown> }> = [];

    if (this.mode === "fail") {
      error = "mock provider error: every request fails in 'fail' mode";
    } else if (this.mode === "mirror") {
      const historical = (testCase?.historical ?? {}) as any;
      text = `MOCK MIRROR: ${(historical.text ?? "").slice(0, 300)}`;
      toolCalls = (historical.tool_calls ?? []).map((call) => ({ name: call.name, arguments: { ...call.arguments } }));
      error = historical.error ?? null;
    } else if (this.mode === "echo") {
      const lastUser = [...(request.messages ?? [])].reverse().find((message) => message.role === "user");
      text = `MOCK ECHO: ${(lastUser?.content ?? "").slice(0, 400)}`;
    } else {
      text = this.text ?? "MOCK: canned response";
      if (this.mode === "canned") toolCalls = this.toolCalls.map((call) => ({ ...call }));
    }

    const latency = Date.now() - started + 1;
    return result(this.name, {
      text,
      tool_calls: toolCalls,
      finish_reason: "stop",
      error,
      latency_ms: latency,
      usage: error ? unknownUsage() : estimateUsage(text),
      model: this.model,
    });
  }
}

// ---------------------------------------------------------------------------
// OpenAI-compatible (with presets)
// ---------------------------------------------------------------------------

export const PROVIDER_PRESETS: Record<string, { base_url: string; default_model: string | null; key_envs: string[]; note: string }> = {
  grok: { base_url: "https://api.x.ai/v1", default_model: "grok-4", key_envs: ["XAI_API_KEY", "GROK_API_KEY"], note: "xAI Grok via its OpenAI-compatible endpoint." },
  groq: { base_url: "https://api.groq.com/openai/v1", default_model: "llama-3.3-70b-versatile", key_envs: ["GROQ_API_KEY"], note: "Groq's OpenAI-compatible endpoint." },
  deepseek: { base_url: "https://api.deepseek.com/v1", default_model: "deepseek-chat", key_envs: ["DEEPSEEK_API_KEY"], note: "DeepSeek's OpenAI-compatible endpoint." },
  mistral: { base_url: "https://api.mistral.ai/v1", default_model: "mistral-large-latest", key_envs: ["MISTRAL_API_KEY"], note: "Mistral's OpenAI-compatible endpoint." },
  openrouter: { base_url: "https://openrouter.ai/api/v1", default_model: "openai/gpt-4o-mini", key_envs: ["OPENROUTER_API_KEY"], note: "OpenRouter routes to hundreds of models; model ids are provider-prefixed." },
  together: { base_url: "https://api.together.xyz/v1", default_model: "meta-llama/Llama-3.3-70B-Instruct-Turbo", key_envs: ["TOGETHER_API_KEY"], note: "Together AI; common home for hosted Llama models." },
  fireworks: { base_url: "https://api.fireworks.ai/inference/v1", default_model: "accounts/fireworks/models/llama-v3p3-70b-instruct", key_envs: ["FIREWORKS_API_KEY"], note: "Fireworks AI; also hosts Llama models." },
  ollama: { base_url: "http://localhost:11434/v1", default_model: "llama3.2", key_envs: [], note: "Ollama on localhost — a free, fully local live target. No key needed." },
  vllm: { base_url: "http://localhost:8000/v1", default_model: null, key_envs: [], note: "Local vLLM server; pass a target model (it serves whatever you loaded)." },
};

export function listPresets() {
  return structuredClone(PROVIDER_PRESETS);
}

export interface OpenAIConfig {
  model?: string | null;
  base_url?: string | null;
  api_key?: string | null;
  timeout?: number;
  label?: string;
  key_envs?: string[];
}

export class OpenAICompatibleAdapter {
  name: string;
  kind = "live";
  remote: boolean;
  model: string;
  baseUrl: string;
  timeout: number;
  label: string;
  keyEnvs: string[];

  constructor(config: OpenAIConfig = {}, remote = false) {
    this.model = config.model ?? "gpt-4o-mini";
    this.baseUrl = (config.base_url ?? "https://api.openai.com/v1").replace(/\/$/, "");
    this.apiKey = config.api_key ?? null;
    this.timeout = config.timeout ?? 60;
    this.remote = remote;
    this.label = config.label ?? "openai";
    this.keyEnvs = config.key_envs ?? ["SWITCHIT_OPENAI_API_KEY", "OPENAI_API_KEY"];
  }

  private apiKey: string | null;

  capabilities() {
    return {
      tool_calling: true,
      structured_output: true,
      streaming: true,
      caching: null,
      max_context_tokens: null,
      notes: "Declared for OpenAI-compatible chat completions. Verify against the provider's current documentation.",
    };
  }

  private requireRemote() {
    if (!this.remote) {
      throw new RemoteDisabledError(
        `The '${this.label}' adapter is a live provider. It is disabled by default; re-run the replay command with --remote to enable data transfer to this provider.`,
      );
    }
    if (!this.apiKey) {
      throw new ProviderError(
        `Missing API key for '${this.label}'. Set ${this.keyEnvs.join(" or ")} in the environment; keys are never accepted on the command line.`,
      );
    }
  }

  static toMessages(messages: ReplayRequest["messages"]) {
    const converted: Array<Record<string, unknown>> = [];
    const pending: string[] = [];
    for (const message of messages ?? []) {
      const content = message.content ?? "";
      if (message.role === "assistant") {
        const entry: Record<string, unknown> = { role: "assistant", content: content || null };
        const historical = (message.tool_calls as Array<{ name: string; arguments: unknown }>) ?? [];
        if (historical.length) {
          const mapped = historical.map((call, position) => {
            const id = `call_${pending.length + position}`;
            pending.push(id);
            return { id, type: "function", function: { name: call.name, arguments: JSON.stringify(call.arguments ?? {}) } };
          });
          entry.tool_calls = mapped;
        }
        converted.push(entry);
      } else if (message.role === "tool") {
        converted.push({
          role: "tool",
          tool_call_id: message.tool_call_id ?? pending.shift() ?? `call_${converted.length}`,
          content,
        });
      } else {
        converted.push({ role: message.role === "system" ? "system" : "user", content });
      }
    }
    return converted;
  }

  async replay(request: ReplayRequest, _testCase?: CaseLike): Promise<ReplayResult> {
    this.requireRemote();
    const started = Date.now();
    const payload: Record<string, unknown> = {
      model: this.model,
      messages: OpenAICompatibleAdapter.toMessages(request.messages ?? []),
    };
    if (request.tools?.length) {
      payload.tools = request.tools.map((tool) => ({
        type: "function",
        function: { name: tool.name, description: "", parameters: { type: "object", "properties": {} } },
      }));
    }
    let body: Record<string, any>;
    try {
      body = (await postJson(
        `${this.baseUrl}/chat/completions`,
        payload,
        { Authorization: `Bearer ${this.apiKey}`, "Content-Type": "application/json", "User-Agent": "switchit-harness/0.1" },
        this.timeout * 1000,
      )) as Record<string, any>;
    } catch (error) {
      return this.errorResult(error, started);
    }
    const latency = Date.now() - started;
    return this.parse(body, latency);
  }

  parse(body: Record<string, any>, latency: number): ReplayResult {
    const choices = body.choices ?? [];
    const choice = choices[0] ?? {};
    const message = choice.message ?? {};
    const toolCalls = (message.tool_calls ?? []).map((call: any) => {
      const fn = call.function ?? {};
      let args: unknown = {};
      try {
        args = JSON.parse(fn.arguments ?? "{}");
      } catch {
        args = { _raw: String(fn.arguments ?? "").slice(0, 400) };
      }
      return { name: fn.name ?? "", arguments: args as Record<string, unknown> };
    });
    const usage = body.usage ?? {};
    const hasUsage = usage.prompt_tokens != null || usage.completion_tokens != null;
    return result(this.name, {
      text: message.content ?? "",
      tool_calls: toolCalls,
      finish_reason: choice.finish_reason ?? null,
      error: choices.length ? null : "response contained no choices",
      latency_ms: latency,
      usage: hasUsage
        ? { input_tokens: usage.prompt_tokens ?? null, output_tokens: usage.completion_tokens ?? null, basis: "recorded", estimator: null }
        : unknownUsage(),
      model: body.model ?? this.model,
    });
  }

  private errorResult(error: unknown, started: number): ReplayResult {
    const message = `${(error as Error).name} contacting ${this.label}: ${(error as Error).message}`;
    return result(this.name, {
      text: "", tool_calls: [], finish_reason: null, error: message,
      latency_ms: Date.now() - started, usage: unknownUsage(), model: this.model,
    });
  }
}

// ---------------------------------------------------------------------------
// Anthropic Messages
// ---------------------------------------------------------------------------

export class AnthropicAdapter {
  name = "anthropic";
  kind = "live";
  remote: boolean;
  model: string;
  private apiKey: string | null;
  private baseUrl: string;
  private timeout: number;
  private maxTokens: number;

  constructor(config: { model?: string; api_key?: string | null; base_url?: string | null; timeout?: number; max_tokens?: number } = {}, remote = false) {
    this.model = config.model ?? "claude-sonnet-4-5";
    this.apiKey = config.api_key ?? null;
    this.baseUrl = (config.base_url ?? "https://api.anthropic.com").replace(/\/$/, "");
    this.timeout = config.timeout ?? 60;
    this.remote = remote;
    this.maxTokens = config.max_tokens ?? 1024;
  }

  capabilities() {
    return {
      tool_calling: true,
      structured_output: true,
      streaming: true,
      caching: true,
      max_context_tokens: null,
      notes: "Declared for the Messages API. Verify against Anthropic's current documentation.",
    };
  }

  private requireRemote() {
    if (!this.remote) {
      throw new RemoteDisabledError(
        "The 'anthropic' adapter is a live provider. It is disabled by default; re-run the replay command with --remote to enable data transfer to this provider.",
      );
    }
    if (!this.apiKey) {
      throw new ProviderError(
        "Missing API key for 'anthropic'. Set SWITCHIT_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY in the environment; keys are never accepted on the command line.",
      );
    }
  }

  async replay(request: ReplayRequest, _testCase?: CaseLike): Promise<ReplayResult> {
    this.requireRemote();
    const started = Date.now();
    const systemParts: string[] = [];
    const converted: Array<Record<string, string>> = [];
    for (const message of request.messages ?? []) {
      const content = message.content ?? "";
      if (message.role === "system") {
        if (content) systemParts.push(content);
      } else if (message.role === "tool") {
        converted.push({ role: "user", content: `[tool result] ${content}` });
      } else if (message.role === "assistant") {
        converted.push({ role: "assistant", content: content || "(assistant tool call)" });
      } else {
        converted.push({ role: "user", content });
      }
    }
    const payload: Record<string, unknown> = {
      model: this.model,
      max_tokens: this.maxTokens,
      messages: converted.length && converted[0].role === "user" ? converted : [{ role: "user", content: "(context)" }, ...converted],
    };
    if (systemParts.length) payload.system = systemParts.join("\n");
    if (request.tools?.length) {
      payload.tools = request.tools.map((tool) => ({
        name: tool.name,
        description: "",
        input_schema: { type: "object", properties: {} },
      }));
    }
    let body: Record<string, any>;
    try {
      body = (await postJson(
        `${this.baseUrl}/v1/messages`,
        payload,
        {
          "x-api-key": this.apiKey ?? "",
          "anthropic-version": "2023-06-01",
          "Content-Type": "application/json",
          "User-Agent": "switchit-harness/0.1",
        },
        this.timeout * 1000,
      )) as Record<string, any>;
    } catch (error) {
      return result(this.name, {
        text: "", tool_calls: [], finish_reason: null,
        error: `${(error as Error).name} contacting anthropic: ${(error as Error).message}`,
        latency_ms: Date.now() - started, usage: unknownUsage(), model: this.model,
      });
    }
    const latency = Date.now() - started;
    const textParts: string[] = [];
    const toolCalls: Array<{ name: string; arguments: Record<string, unknown> }> = [];
    for (const block of body.content ?? []) {
      if (block?.type === "text") textParts.push(block.text ?? "");
      else if (block?.type === "tool_use") toolCalls.push({ name: block.name ?? "", arguments: block.input ?? {} });
    }
    const usage = body.usage ?? {};
    const hasUsage = usage.input_tokens != null || usage.output_tokens != null;
    return result(this.name, {
      text: textParts.join(""),
      tool_calls: toolCalls,
      finish_reason: body.stop_reason ?? null,
      error: (body.content ?? []).length ? null : "response contained no content blocks",
      latency_ms: latency,
      usage: hasUsage
        ? { input_tokens: usage.input_tokens ?? null, output_tokens: usage.output_tokens ?? null, basis: "recorded", estimator: null }
        : unknownUsage(),
      model: body.model ?? this.model,
    });
  }
}

export function buildAdapter(kind: string, config: Record<string, unknown> = {}, remote = false) {
  if (kind in PROVIDER_PRESETS) {
    const preset = PROVIDER_PRESETS[kind];
    const model = (config.model as string) || preset.default_model;
    if (!model) throw new ProviderError(`The '${kind}' preset serves whatever model you loaded; pass one with --target-model (CLI) or target_model (MCP).`);
    return new OpenAICompatibleAdapter(
      {
        model,
        base_url: (config.base_url as string) ?? preset.base_url,
        api_key: (config.api_key as string) ?? null,
        key_envs: preset.key_envs,
        label: kind,
        timeout: (config.timeout as number) ?? 60,
      },
      remote,
    );
  }
  switch (kind) {
    case "recorded":
      return new RecordedBaselineAdapter();
    case "mock":
      return new MockAdapter(
        (config.mock_mode as MockMode) ?? "mirror",
        (config.mock_text as string) ?? null,
        (config.mock_tool_calls as Array<{ name: string; arguments: Record<string, unknown> }>) ?? [],
        (config.model as string) ?? "mock-model",
      );
    case "openai":
      return new OpenAICompatibleAdapter(config as OpenAIConfig, remote);
    case "anthropic":
      return new AnthropicAdapter(config as Record<string, never>, remote);
    default:
      throw new ProviderError(
        `Unknown adapter or preset ${kind}; known kinds: anthropic, mock, openai, recorded; known presets: ${Object.keys(PROVIDER_PRESETS).sort().join(", ")}`,
      );
  }
}

export async function postJson(url: string, payload: unknown, headers: Record<string, string>, timeoutMs: number): Promise<unknown> {
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${text.slice(0, 300)}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new ProviderError(`${url} returned non-JSON response: ${text.slice(0, 200)}`);
  }
}
