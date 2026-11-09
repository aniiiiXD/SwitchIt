# SwitchIt: short pitches and reasons to buy

9 November 2026. Based on the [agent research](agent-workflow-research-2026-11-09.md) and [prospectus](switchit-prospectus-2026-11-09.md).

These are proposed offers to validate, not shipped features. Current SwitchIt is an early import/replay/report harness. Production offers depend on repairing known replay issues; native plugins and full workflow verification need more implementation.

**Recommended line:** “Test the workflows you rely on before you change your agent.”

**Longer-term line:** “Keep your workflows. Change your agent—with evidence of what still works.”

| Priority | Short pitch | Who uses it, and why | Readiness |
| --- | --- | --- | --- |
| 1 | **Before you update Hermes, check your daily workflows.** Replay important tasks against the candidate setup and see what changed before relying on it. | Operators and agencies: less time rediscovering broken prompts, tools, and delivery paths. | Comparison core exists; Hermes capture and isolated execution are new. |
| 2 | **Find the cheapest model that passes your workflow tests.** Compare your own jobs against your quality requirements and measured cost. | Users with recurring inference spend: make a task-specific purchasing decision. | Close to core; needs accurate accounting, pricing inputs, repeat runs, and checks. No savings guarantee. |
| 3 | **Deliver an automation your client can verify.** Include its requirements, test cases, and rerunnable acceptance report in the handover. | Agencies: clearer acceptance and faster diagnosis of later regressions. | Start as an assisted service; client packaging and business checks are new. |
| 4 | **Your agent says it learned something. Test whether it got better.** Compare a proposed skill revision with its previous version on tasks you care about. | Hermes owners and skill authors: catch regressions from agent-written changes. | Needs versioned skill runs; complement native approval and evals. |
| 5 | **Know when a scheduled job completes the wrong task.** Check destination, source freshness, and output—not just whether the run ended. | OpenClaw/Hermes operators: catch incomplete reports and misrouted digests earlier. | Monitoring and observed-result adapters are new. |
| 6 | **Move one workflow from OpenClaw to Hermes with a comparison report.** Identify missing tools and task failures before retiring the old setup. | Users changing runtimes: reduce rebuilding and equivalence guesswork. | Longer-term; separate host runners and explicit mappings. No universal one-click migration. |
| 7 | **Give your company brain procedures with test history.** Attach reviewed requirements and evidence to workflows employees reuse. | Company-assistant builders: know which version worked under which conditions. | Registry/memory integration is proposed; keep canonical artifacts versioned outside semantic search. |
| 8 | **Publish a skill with a compatibility report.** Show which agent/model combinations passed your examples and what remains unsupported. | Authors and platform teams: reduce setup guesswork and support questions. | Requires a runtime matrix; bounded evidence, not certification. |
| 9 | **Check the handoff before it breaks the whole job.** Test that one agent gives the next the required files, identifiers, and completed work. | Delegated coding/operations teams: locate missing intermediate results. | Needs linked events, artifact checks, and host runners. |
| 10 | **Turn a costly failure into a regression test.** Preserve its triggering case and check it again after the fix. | Application developers: reduce recurrence and manual retesting. | Close to core, but crowded: Tracely, BotGauge, and others offer related capabilities. |

## Three offers to test first

**Fastest from today's code: model comparison for one workflow.** Sell a reviewed assessment with tested cases, quality results, complete accounting, and remaining unknowns. Live candidate testing incurs inference costs. Do not promise automated routing or a percentage saving.

**Best initial ecosystem story: a Hermes workflow check.** Its documented plugin/hooks surfaces provide an integration path. Begin with selected capture and user-requested comparisons. No native SwitchIt plugin is installed or validated yet.

**Clearest proposed paid buyer: an automation agency.** Test whether acceptance evidence and reusable maintenance checks matter enough to pay for. Start with one client workflow and a report before building a dashboard.

These rankings are product judgment, not a measured market survey.

## Discovery language

- **Hermes operator:** “What breaks when you update Hermes or change models? We’re building a way to check important workflows against the new setup before you depend on it.”
- **Agency:** “How do you prove an agent workflow meets a client's requirements—and test it again after changes? SwitchIt could package that evidence with delivery.”
- **Company-assistant builder:** “How do you test that your assistant still takes the right actions after a model or memory change? That’s the workflow layer we’re building.”

## Claims to avoid

Do not promise an agent will never break, a saved transcript proves success, every skill transfers unchanged, or fixture replay verifies live business effects. Do not sell a backup, scheduler, or basic skill evaluator as novel when the host already provides it.

The testable promise: make a particular change easier to evaluate against the user's task requirements, with less work than maintaining their existing checks.
