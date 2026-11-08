'use client';

import { useState } from 'react';
import styles from './product-story.module.css';

const steps = [
  { title: 'Import', heading: 'Start with the work already happening.', description: 'Bring your traces and app configuration together. SwitchIt can also read coding-agent sessions already on your machine.', file: 'session.jsonl', lines: ['{ "prompt": "Find the customer order",', '  "tool": "lookup_order",', '  "result": "Order found" }'], caption: 'Sample imported trace' },
  { title: 'Replay', heading: 'Put the same cases through a new route.', description: 'Select representative cases and replay them against a recorded baseline and your target. Tool proposals are captured, never executed.', file: 'replay.case', lines: ['case        find_customer_order', 'baseline    recorded response', 'target      selected provider', 'tool call   captured for comparison'], caption: 'Sample replay case' },
  { title: 'Compare', heading: 'See what changed, and what needs a closer look.', description: 'Compare task outcomes, tool behavior, latency, and token usage. Explicit checks help establish whether the result actually succeeded.', file: 'report.html', lines: ['check       order was found', 'baseline    recorded', 'target      response received', 'verdict     pass / fail / uncertain'], caption: 'Sample comparison' },
];
const outcomes = [
  { title: 'Pass', text: 'The case met its checks.', tone: 'pass' },
  { title: 'Fail', text: 'A check found a meaningful difference.', tone: 'fail' },
  { title: 'Error', text: 'The replay could not complete.', tone: 'error' },
  { title: 'Uncertain', text: 'There isn’t enough evidence to decide.', tone: 'uncertain' },
];

export function SourceStrip() {
  return <section id="sources" className={styles.sources} aria-label="Supported coding agent sources"><div className={styles.inner}><p>Start with sessions you already have</p><div className={styles.sourceNames}><span>Codex</span><span>Claude Code</span><span>Cursor</span><span>OpenCode</span><span>Cline</span><span>Gemini CLI</span><span>Copilot CLI</span></div></div></section>;
}

export function WorkflowSection() {
  const [active, setActive] = useState(0);
  const step = steps[active];
  return <section id="how" className={styles.workflow}><div className={styles.inner}>
    <div className={styles.workflowHead}><p className={styles.label}>How it works</p><h2>A clearer path from<br />one model to another.</h2><p>Follow the evidence through each stage of your migration.</p></div>
    <div className={styles.explorer}>
      <div className={styles.tabs} role="tablist" aria-label="Migration stages">{steps.map((item, index) => <button type="button" role="tab" aria-selected={active === index} aria-controls="workflow-panel" id={`workflow-tab-${index}`} tabIndex={active === index ? 0 : -1} onClick={() => setActive(index)} onKeyDown={(event) => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); const next = (index + (event.key === 'ArrowRight' ? 1 : 2)) % steps.length; setActive(next); document.getElementById(`workflow-tab-${next}`)?.focus(); } }} key={item.title}><span>0{index + 1}</span>{item.title}</button>)}</div>
      <div id="workflow-panel" role="tabpanel" aria-labelledby={`workflow-tab-${active}`} className={styles.explorerBody}>
        <div className={styles.stepCopy}><span className={styles.stepCount}>0{active + 1} / 03</span><h3>{step.heading}</h3><p>{step.description}</p><a href="#demo" className={styles.link}>Explore the replay <span aria-hidden="true">↗</span></a></div>
        <div className={styles.traceVisual}><div className={styles.traceTop}><span><i />{step.file}</span><span>Illustrative sample</span></div><div className={styles.traceLines}>{step.lines.map((line, index) => <div key={line}><span>{String(index + 1).padStart(2, '0')}</span><code>{line}</code></div>)}</div><div className={styles.traceFoot}><span>{step.caption}</span><span>SwitchIt / {step.title}</span></div></div>
      </div>
    </div>
  </div></section>;
}

export function OutcomeSection() {
  return <section id="verdicts" className={styles.outcomes}><div className={styles.outcomeInner}><div className={styles.outcomeHead}><p className={styles.label}>The migration report</p><h2>Know what the<br />evidence says.</h2><p>A useful report makes room for uncertainty. See what passed, what changed, and what needs a closer look.</p><a className={styles.link} href="/guide/">Read the guide <span aria-hidden="true">↗</span></a></div><div className={styles.outcomeGrid}>{outcomes.map((item) => <article key={item.title}><div className={styles.outcomeTitle}><i className={styles[item.tone]} /><h3>{item.title}</h3></div><p>{item.text}</p></article>)}</div><p className={styles.outcomeNote}>Add explicit checks for the outcomes that matter. A historical response alone isn’t proof of success.</p></div></section>;
}

export function TargetsSection() {
  return <section id="targets" className={styles.targets}><div className={styles.inner}><div className={styles.targetsHead}><div><p className={styles.label}>Providers and protocols</p><h2>Your workflow.<br />Your direction.</h2></div><p>Compare against a recorded baseline, then replay with the provider that fits your work.</p></div><div className={styles.routing}><div className={styles.routeOrigin}><span>Starting point</span><strong>Recorded<br />baseline</strong><p>Your existing workflow, captured as evidence.</p></div><div className={styles.routeConnector} aria-hidden="true"><span>→</span></div><div className={styles.routeGroups}><div><p>OpenAI-compatible</p><div className={styles.pills}><span>OpenAI</span><span>xAI</span><span>Groq</span><span>DeepSeek</span><span>Mistral</span><span>OpenRouter</span><span>Together</span><span>Fireworks</span></div></div><div><p>Native adapters</p><div className={styles.pills}><span>Anthropic Messages</span><span>Amazon Bedrock Converse</span></div></div><div><p>Local and offline</p><div className={styles.pills}><span>Ollama</span><span>vLLM</span><span>Mock target</span></div></div></div></div></div></section>;
}

export default function ProductStory() { return <><SourceStrip /><WorkflowSection /><OutcomeSection /><TargetsSection /></>; }
