import type { Metadata } from "next";
export const metadata:Metadata={title:"Getting started | SwitchIt!"};
export default function Guide() {
 return <main id="main-content" className="guide">
  <a className="guide-back" href="/">← Back to SwitchIt!</a>
  <h1>Your first switch starts here.</h1>
  <p className="intro">Run a complete migration comparison on synthetic workflows. Everything stays local, and you don’t need a provider account.</p>
  <section><h2>Install SwitchIt!</h2><p>You’ll need Python 3.10 or newer. Download the package, then run this from your downloads folder. A virtual environment keeps the install separate from your other projects.</p><a className="guide-download" href="/downloads/switchit_harness-0.1.0-py3-none-any.whl" download>Download v0.1.0 package</a><pre><code>{[
   "python3 -m venv .venv",
   "# macOS / Linux",
   "source .venv/bin/activate",
   "# Windows PowerShell: .venv\\Scripts\\Activate.ps1",
   "python -m pip install ./switchit_harness-0.1.0-py3-none-any.whl"
  ].join("\n")}</code></pre><p>Already have the source repository? Run <code>python -m pip install .</code> from its root instead.</p></section>
  <section><h2>Run the offline demo</h2><pre><code>switchit run --demo --state-dir .switchit-demo</code></pre><p>Open <code>.switchit-demo/run/report.html</code> in your browser. The bundled traces and mock target let you explore the workflow without sending data to a provider. The report includes passes, failures, errors, and uncertainties.</p></section>
  <section><h2>Bring your own workflows</h2><p>Import authorized local coding-agent sessions, then review the workflow map and define success checks before replaying.</p><pre><code>{[
   "switchit import --agents --state-dir .switchit",
   "switchit profile --state-dir .switchit",
   "switchit map --state-dir .switchit",
   "switchit cases --state-dir .switchit",
   "# Review workflows.json and cases.json in .switchit first.",
   "switchit replay --state-dir .switchit",
   "switchit report --state-dir .switchit"
  ].join("\n")}</code></pre><p>Local imports support Codex, Claude Code, OpenCode, Cursor, Cline, Gemini CLI, and Copilot CLI. Exported JSON/JSONL, OTLP, Langfuse, and Parquet are supported too; Parquet needs the optional dependency.</p></section>
  <section><h2>Choose a live target when you’re ready</h2><p>Live replay requires explicit opt-in with <code>--remote</code>. It sends the redacted cases to your chosen provider and can incur usage charges. Supply credentials through environment variables. OpenAI-compatible APIs, Anthropic Messages, and Bedrock Converse are supported, along with local Ollama and vLLM targets.</p><p>The baseline always comes from recorded history. Tool calls are compared against historical fixtures; the harness never executes your live business tools. A passing check only establishes the behavior that check measures.</p></section>
  <section><h2>Let your agent drive</h2><p>Register the built-in MCP server in your agent’s configuration. It exposes import, profile, map, cases, replay, report, run, status, read_report, and list_providers.</p><pre><code>{JSON.stringify({mcpServers:{switchit:{command:"switchit-mcp",args:[]}}},null,2)}</code></pre><p>The companion <code>$switchit</code> skill guides the review of workflows, success checks, and migration findings.</p></section>
 </main>;
}
