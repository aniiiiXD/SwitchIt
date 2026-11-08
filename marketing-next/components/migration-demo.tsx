"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./migration-demo.module.css";

type Verdict = "Pass" | "Fail" | "Uncertain";
const cases = [
  { name: "Answer a support question", check: "Response check", evidence: "The sample response addresses the question and includes the expected next step." },
  { name: "Look up an account", check: "Tool behavior", evidence: "The sample checks whether the proposed lookup matches the recorded tool request." },
  { name: "Summarize a conversation", check: "Content check", evidence: "The sample checks for the key points in a fictional conversation." },
];
const outcomes: Record<string, Verdict[]> = {
  Anthropic: ["Pass", "Uncertain", "Pass"],
  OpenAI: ["Pass", "Pass", "Fail"],
  Ollama: ["Uncertain", "Pass", "Pass"],
};

export function MigrationPreview() {
  const [target, setTarget] = useState("Anthropic");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<{ target: string; verdicts: Verdict[] } | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  function run() {
    setRunning(true);
    setResult(null);
    setExpanded(null);
    timer.current = setTimeout(() => {
      setResult({ target, verdicts: outcomes[target] });
      setRunning(false);
    }, 850);
  }
  return (
    <div className={styles.preview}>
      <div className={styles.windowTop}>
        <div className={styles.windowTitle}><span className={styles.windowMark}>↗</span> Replay workspace</div>
        <span className={styles.samplePill}><span /> Interactive sample <span className={styles.sampleDivider}>/</span> Synthetic and offline</span>
      </div>
      <div className={styles.previewBody}>
        <div className={styles.previewIntro}><div><span className={styles.microLabel}>Sample migration</span><strong>Compare the work that matters.</strong><p>Choose a target and explore an illustrative replay.</p></div><span className={styles.caseCount}>3 sample cases</span></div>
        <div className={styles.route}>
          <div className={styles.routeNode}><span className={styles.nodeIcon}>▤</span><span><small>Reference</small><b>Recorded workflows</b><em>Captured behavior</em></span></div>
          <div className={styles.routeLine}><span>•••</span><i>↗</i><span>•••</span></div>
          <div className={`${styles.routeNode} ${styles.targetNode}`}><span className={styles.targetIcon}>✳</span><span><small>Target</small><b>{target}</b><em>Illustrative results</em></span></div>
        </div>
        <div className={styles.controls}><label htmlFor="sample-target">Target provider</label><select id="sample-target" value={target} disabled={running} onChange={(event) => { setTarget(event.target.value); setResult(null); setExpanded(null); }}><option>Anthropic</option><option>OpenAI</option><option>Ollama</option></select><button onClick={run} disabled={running}>{running ? "Replaying…" : "Run sample replay"}<span aria-hidden="true"> ↗</span></button></div>
        <div className={styles.tableTitle}><span>Replay cases</span><span>{result ? `${result.verdicts.filter((v) => v === "Pass").length} pass / ${result.verdicts.filter((v) => v === "Fail").length} fail / ${result.verdicts.filter((v) => v === "Uncertain").length} uncertain` : "Select a case to inspect its check"}</span></div>
        <div className={styles.resultsHeader}><span>Case</span><span>Check</span><span className={styles.referenceHeader}>Reference</span><span>Replay</span><span /></div>
        <div className={styles.results} aria-live="polite" aria-busy={running}>
          {cases.map((item, index) => <div key={item.name} className={styles.caseBlock}><button className={styles.resultRow} aria-expanded={expanded === index} onClick={() => setExpanded(expanded === index ? null : index)}><span className={styles.caseName}><span className={styles.caseNumber}>0{index + 1}</span>{item.name}</span><span className={styles.checkType}>{item.check}</span><span className={styles.reference}>Recorded</span><span className={result ? `${styles.verdict} ${styles[result.verdicts[index].toLowerCase()]}` : styles.waiting}>{running ? "Running…" : result ? result.verdicts[index] : "—"}</span><span className={styles.expandIcon} aria-hidden="true">{expanded === index ? "−" : "+"}</span></button>{expanded === index && <div className={styles.evidence}><span>Illustrative check</span><p>{item.evidence} {result ? `The fictional ${result.target} replay is marked ${result.verdicts[index].toLowerCase()} for this example.` : "Run the sample replay to see a fictional verdict."}</p></div>}</div>)}
        </div>
        <div className={styles.previewFoot}><span><span className={styles.tinyDot} /> {result ? `Illustrative results for ${result.target}` : "Ready to replay 3 sample cases"}</span><span>Local demo</span></div>
      </div>
    </div>
  );
}

const commands = {
  "Install (PyPI)": "pip install switchit-harness",
  "Install (npm)": "npm install switchit-harness",
  "Agent skill": "npx switchit-skill",
  "Run demo": "switchit run --demo --state-dir .switchit-demo",
};
export function InstallPanel() {
  const [tab, setTab] = useState<keyof typeof commands>("Install (PyPI)");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(commands[tab]);
      setCopyState("copied");
    } catch { setCopyState("failed"); }
  }
  function choose(next: keyof typeof commands) { setTab(next); setCopyState("idle"); }
  return <section id="install" className={styles.install}><div className={styles.installInner}>
    <div className={styles.installCopy}><span className={styles.installEyebrow}><span /> Get started</span><h2>Make your next switch with evidence.</h2><p>Grab the package from PyPI or npm and try the bundled synthetic demo. No credentials needed. The demo command below ships inside both packages.</p><div className={styles.installLinks}><a href="https://pypi.org/project/switchit-harness/" target="_blank" rel="noreferrer">PyPI — switchit-harness <span aria-hidden="true">↗</span></a><a href="https://www.npmjs.com/package/switchit-harness" target="_blank" rel="noreferrer">npm — switchit-harness <span aria-hidden="true">↗</span></a><a href="https://www.npmjs.com/package/switchit-skill" target="_blank" rel="noreferrer">npm — switchit-skill <span aria-hidden="true">↗</span></a><a href="https://github.com/aniiiiXD/SwitchIt" target="_blank" rel="noreferrer">GitHub <span aria-hidden="true">↗</span></a><a href="/guide/">Read the setup guide <span aria-hidden="true">↗</span></a></div></div>
    <div className={styles.terminal}><div className={styles.terminalTop}><div className={styles.tabs} aria-label="Command">{Object.keys(commands).map((label) => <button key={label} className={tab === label ? styles.activeTab : ""} aria-pressed={tab === label} onClick={() => choose(label as keyof typeof commands)}>{label}</button>)}</div><span>terminal</span></div><div className={styles.command}><span aria-hidden="true">$</span><code>{commands[tab]}</code><button onClick={copy}>{copyState === "copied" ? "Copied" : copyState === "failed" ? "Copy failed" : "Copy"}</button></div><p className={styles.terminalNote} aria-live="polite">{copyState === "failed" ? "Select the command and copy it manually." : tab === "Agent skill" ? "Installs the $switchit skill for agents that read ~/.agents/skills." : tab === "Run demo" ? "Runs locally with synthetic traces — no credentials, no network." : tab === "Install (npm)" ? "Node 20+. Adds the switchit and switchit-mcp commands." : "Python 3.10+. Core is stdlib-only; extras (pyarrow, boto3) are opt-in."}</p></div>
  </div></section>;
}
