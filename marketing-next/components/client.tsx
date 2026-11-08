"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./hero.module.css";

const HERO_COMMANDS = [
  { label: "pip", text: "pip install switchit-harness" },
  { label: "npm", text: "npm install switchit-harness" },
  { label: "demo", text: "switchit run --demo" },
];

export function HeroInstallCard() {
  const [copied, setCopied] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function copy(command: string) {
    void navigator.clipboard?.writeText(command).catch(() => undefined);
    setCopied(command);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(null), 1600);
  }

  return (
    <div className={styles.installCard} aria-label="Install SwitchIt">
      <p className={styles.installLabel}>Get started — 10 seconds, no credentials</p>
      {HERO_COMMANDS.map(({ label, text }) => (
        <div className={styles.cmdRow} key={label}>
          <code className={styles.cmdText}><span aria-hidden="true">$ </span>{text}</code>
          <button type="button" className={styles.copyBtn} onClick={() => copy(text)}>
            {copied === text ? "Copied ✓" : "Copy"}
          </button>
        </div>
      ))}
      <a
        className={styles.downloadBtn}
        href="https://pypi.org/project/switchit-harness/"
        target="_blank"
        rel="noreferrer"
      >
        Download from PyPI <span aria-hidden="true">↗</span>
      </a>
      <div className={styles.miniLinks}>
        <a href="https://www.npmjs.com/package/switchit-harness" target="_blank" rel="noreferrer">npm harness ↗</a>
        <a href="https://www.npmjs.com/package/switchit-skill" target="_blank" rel="noreferrer">agent skill ↗</a>
        <a href="https://github.com/aniiiiXD/SwitchIt" target="_blank" rel="noreferrer">GitHub ↗</a>
      </div>
    </div>
  );
}

export function CopyButton({ text, label = "Copy install command" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).catch(() => undefined);
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1600);
      }}
      className="inline-flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm font-semibold text-white outline outline-1 outline-offset-[-1px] outline-white/75 backdrop-blur transition-colors duration-150 hover:bg-white/15"
      style={{ backgroundColor: "rgba(247,247,247,0.05)" }}
    >
      {copied ? "Copied ✓" : label}
    </button>
  );
}

export function Reveal({ children, className = "", delay = 0 }: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setShown(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setShown(true);
            observer.disconnect();
          }
        }
      },
      { threshold: 0.12 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      ref={ref}
      className={`${className} transition-all duration-[600ms] ease-out ${
        shown ? "translate-y-0 opacity-100" : "translate-y-5 opacity-0"
      }`}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
}
