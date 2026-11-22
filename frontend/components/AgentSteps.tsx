"use client";

import { useState } from "react";

import type { Step } from "@/lib/api";

const ICONS: Record<string, string> = {
  rewrite: "✏️",
  plan: "🧭",
  search: "📄",
  web: "🌐",
  tool: "🛠️",
  grade: "✅",
  answer: "💬",
  error: "⚠️",
};

type Props = { steps: Step[]; live: boolean };

export default function AgentSteps({ steps, live }: Props) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;

  const current = [...steps].reverse().find((s) => s.status === "running");
  const summary = live && current ? current.label : `${steps.length} step${steps.length > 1 ? "s" : ""}`;

  return (
    <div className="steps">
      <button className="steps-toggle" onClick={() => setOpen(!open)} aria-expanded={open || live}>
        {live && current ? <span className="spinner" /> : <span>{open ? "▾" : "▸"}</span>}
        <span>{summary}</span>
      </button>
      {(open || live) && (
        <ol className="steps-list">
          {steps.map((s) => (
            <li key={s.id} className={`step ${s.status}`}>
              <span className="step-icon">{s.status === "running" ? "⏳" : ICONS[s.kind] ?? "•"}</span>
              <span className="step-label">{s.label}</span>
              {s.detail && <code className="step-detail">{s.detail}</code>}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
