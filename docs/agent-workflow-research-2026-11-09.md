# SwitchIt: agent ecosystem research

Research date: 9 November 2026. Companion documents: [product prospectus](switchit-prospectus-2026-11-09.md) and [short sales ideas](switchit-sales-ideas.md).

**Recommendation:** investigate Hermes as the first native integration, with a paid pilot aimed at an agency or business running consequential recurring workflows. Test demand for workflow-specific change assessment before building a universal agent migration product.

This extends the preceding Reddit/X/Product Hunt research into Hermes, OpenClaw, NanoClaw, Claude Code, and OpenHands. Documentation establishes product capabilities; community accounts are self-reports; commercial opportunities are hypotheses. No agents were installed, customer accounts accessed, or outreach sent.

## Browser work performed

A live Firecrawl browser opened the official Hermes skills documentation and followed navigation to the Plugins page. A subsequent extraction confirmed its URL and read the plugin registration and installation content. The same browser navigated to OpenClaw's Automations documentation and extracted the rendered article. The session was closed afterward.

Visited pages:

- https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins
- https://docs.openclaw.ai/automation/cron-jobs

Search and web retrieval supplied the linked Reddit discussions, Product Hunt listings, other official guides, and the prior X evidence. This was public-page research, not hands-on agent execution. Browser interaction verified documentation surfaces; it did not verify that a SwitchIt plugin works.

## What people report doing

**Hermes: recurring workflows with several moving parts.** One user described daily news delivery, a WhatsApp-to-Telegram approval flow, and token reports, then repeatedly repairing them after restarts and configuration changes. The user also described frequent model changes, so this does not isolate the cause as a Hermes defect. The post is approximately six months old, not a test of the current release. The product signal is difficulty determining which change broke a workflow. [User report](https://www.reddit.com/r/hermesagent/comments/1t0qg4b/10_days_into_hermes_agent_workflows_keep_breaking/).

**Hermes: scheduled work with recognizable outputs.** Another discussion describes calendar briefings, RSS summaries delivered to Telegram, alerts, and household routines. These suggest pilot shapes whose inputs, destination, and output completeness can be checked. Reported usage and costs are unverified and do not establish a paid market. [Cron-use discussion](https://www.reddit.com/r/hermesagent/comments/1tpms69/what_cron_jobs_do_you_run_with_hermes_agent_heres/).

**OpenClaw: delegation plus scheduled jobs.** A self-described solo operator reports project agents, GitHub work, inbox triage, briefs, and maintenance. This establishes a claimed operating pattern, not verified workload or autonomy. A proposed assessment could map which jobs depend on particular skills, tools, models, and destinations. [Operator account](https://www.reddit.com/r/openclaw/comments/1r2uz4e/openclaw_skills_i_actually_use/).

**Businesses built on agent runtimes.** Product Hunt's Nous Research page includes builder accounts for Iris, MausBot, and Humalike integrations. Agent 37's listing advertises managed persistent agents for customers. These suggest interview segments: company-agent builders and managed-agent operators. They do not prove interest in SwitchIt. [Nous builder accounts](https://www.producthunt.com/products/hermes-4), [Agent 37 Cloud](https://www.producthunt.com/products/agent-37-38?launch=agent-37-cloud).

## Integration assessment

| Ecosystem | Verified surface | Proposed SwitchIt fit | Limitation |
| --- | --- | --- | --- |
| Hermes | Skills, Python plugins, tools, commands, lifecycle hooks | Capture selected executions and associate checks with skill/model changes | Hooks do not automatically reveal external state or guarantee complete capture |
| OpenClaw | Persistent automations, run history, multiple payload/session styles | Assess recurring jobs before configuration changes | Needs a runner/import adapter; scheduling and diagnostics already exist |
| NanoClaw | Container-based runtime and customization through skills | Test a customized installation against intended workflows | Provider, filesystem, and channel differences need explicit mappings |
| Claude Code | Skills, plugins, native evaluation | Reuse native evals and add cross-runtime or broader application evidence | A basic skill evaluator duplicates native functionality |
| OpenHands | SDK, custom tools, persistence, tracing | Import suitable exported traces and compare repository tasks | SwitchIt's OTLP JSON importer is not automatically a live OTLP collector or validated OpenHands adapter |

Sources: [Hermes plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins), [Hermes hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks), [OpenClaw automations](https://docs.openclaw.ai/automation/cron-jobs), [NanoClaw skills model](https://github.com/nanocoai/nanoclaw/blob/main/docs/skills-model.md), [Claude Code skills](https://code.claude.com/docs/en/skills), [OpenHands SDK](https://docs.openhands.dev/sdk), [OpenHands tracing](https://docs.openhands.dev/sdk/guides/observability).

## Hermes: a concrete first plugin path

Official docs describe Python plugins with a manifest and registration function. Plugins can expose tools or commands and subscribe to lifecycle events. The hook catalog includes model/tool boundaries, sessions, skills, and approvals. A prototype must verify payload completeness and coverage on a pinned Hermes release. [Plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins), [hook reference](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks).

Proposed scope: capture one selected workflow; store redacted evidence and configuration versions; have a person review its success checks; run a candidate comparison in an isolated environment; return a report inside Hermes. Do not run slow evaluations inside the live tool-call path. Capture omissions must make the result incomplete rather than silently successful.

This would be a general tools/hooks plugin with an optional bundled skill. It would not replace Hermes's memory backend. MCP can expose SwitchIt's existing assessment tools, but automatic capture needs host instrumentation.

## Existing features change the pitch

Hermes already creates reusable skills and can stage skill writes for approval. Its curator maintains agent-created skills and supports locks, backups, and rollback. SwitchIt should assess whether changes preserve reviewed task requirements, rather than offer another skill store or backup button. [Skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills), [Curator](https://hermes-agent.nousresearch.com/docs/user-guide/features/curator).

Claude Code documents `claude plugin eval`, baseline comparisons, CI thresholds, and skill-creator evaluation. A standalone claim to “test Claude skills” is weak differentiation. Cross-agent evidence, workflow-specific effects, and client acceptance reports would need to justify another tool. [Native evaluation](https://code.claude.com/docs/en/skills#evaluate-and-iterate-on-a-skill).

OpenClaw documents persistent automations and guarded state upgrades. SwitchIt must add checks of the customer's task outcomes rather than claim native mechanisms are missing. [Automations](https://docs.openclaw.ai/automation/cron-jobs), [guarded upgrades](https://docs.openclaw.ai/start/why-openclaw/versioned-state-guarded-upgrades).

NanoClaw's skills model already includes tests for customization components. Preserve those tests and add complete-workflow checks where needed. [Skills model](https://github.com/nanocoai/nanoclaw/blob/main/docs/skills-model.md).

## Evidence retained from the preceding research

| Source | Observed evidence | Implication |
| --- | --- | --- |
| [Reddit: model switches](https://www.reddit.com/r/AI_Agents/comments/1wvah6h/how_are_you_testing_model_switches_for_ai_agents/) | Tool-argument drift and fixture matching; author discloses EvalShift | Concrete change trigger, with direct competition |
| [Reddit: workflow regression](https://www.reddit.com/r/AI_Agents/comments/1up9s9k/how_are_you_regressiontesting_agent_workflows/) | Required behavior, effects, fixture upkeep; author discloses Runme | Minimize noisy transcript differences and maintenance |
| [X: Tracely post](https://x.com/DanKornas/status/2105511507119259989) | Production failures promoted to replay tests | Trace-to-test conversion is already being offered |
| [Product Hunt: BotGauge](https://www.producthunt.com/products/botgauge-2) | Advertised behavior testing, replay, regression checks | General agent reliability is crowded |
| [EvalShift repository](https://github.com/evalshift/evalshift-cli) | Migration and tool-call regression testing | Safe switching needs workflow-specific differentiation |
| [Supermemory update](https://supermemory.ai/blog/an-update-to-supermemory/) | Hosted Company Brain discontinued; API/MCP/plugins continue | Target current infrastructure and independent company assistants |
| [Company Brain open source](https://supermemory.ai/blog/open-sourcing-company-brain/) | Published harness with memory scope, approvals, and recovery | Integration reference, not a hosted-product partnership |
| [MemoryBench](https://supermemory.ai/docs/memorybench/overview) | Memory-provider benchmarking exists | Add downstream business actions and outcomes |

These sources justify investigating the problem. They do not establish market size, conversion, willingness to pay, or competitor performance.

## First pilot and buyer hypothesis

**Offer: “Before you change Hermes, test the workflows you rely on.”**

Start with one agency or operator and three recurring workflows: for example a digest, customer-response draft, and report. Record approved destinations and outputs. Create a small reviewed case set with representative failures. Compare current and candidate configurations without sending real customer messages during fixture tests.

Report configuration problems, behavior changes, missing evidence, and verified outcomes separately. A service restart needs a runtime check; model replay alone cannot diagnose it. A claimed tool success is not proof of delivery; staging validation needs a receipt or inspected result.

Measure time to first useful comparison, actionable and noisy findings, upkeep, and use on the next change. A paid pilot could deliver a report and rerunnable suite. Pricing belongs in interviews; no willingness to pay is established here.

Interview agencies first, then business operators, managed-agent providers, skill publishers, and enthusiasts. Service obligations may create a clearer budget than personal experimentation; this is a hypothesis. Ask about the last actual break, detection, repair effort, and native checks. A second use and payment are stronger signals than enthusiasm.

Builders publicly describing Iris and managed-agent providers such as Agent 37 are research leads only. No affiliation, interest, or contact is implied.

The existing core offers imports, cases, checks, comparisons, and reports. Dedicated adapters, complete state capture, scheduled monitoring, portability packages, and native plugins remain proposed. Repair the replay defects recorded in the [prospectus](switchit-prospectus-2026-11-09.md) before making dependable commercial claims.
