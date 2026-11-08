import { html } from "../ui.js";
import { useEffect, useState } from "react";

const STEPS = [
  { id: "reading", label: "Read the screenplay", who: "code" },
  { id: "candidates", label: "Find every 20–60 s moment", who: "code" },
  { id: "score", label: "Judge hook and cliffhanger", who: "ai" },
  { id: "clarity", label: "Check a newcomer would understand", who: "ai" },
  { id: "rerank", label: "Compare the best clips and rank them", who: "ai" },
  { id: "write", label: "Write the shortlist", who: "code" },
];
const ORDER = ["queued", ...STEPS.map((s) => s.id), "done"];

export const providerName = (model) => {
  const p = (model || "").split("/")[0];
  return p ? p.charAt(0).toUpperCase() + p.slice(1) : "The model";
};

export function phaseTitle(progress) {
  const ai = providerName(progress.model);
  switch (progress.phase) {
    case "queued": return "Waiting for a free analyser…";
    case "reading": return "Reading your screenplay…";
    case "candidates": return "Finding every 20–60 second moment…";
    case "score": return `${ai} is judging each moment's opening hook and cliffhanger…`;
    case "clarity": return `${ai} is checking: would a stranger understand this clip?`;
    case "rerank": return `${ai} is comparing the best clips against each other…`;
    case "write": return "Writing the ranked shortlist…";
    default: return "Finishing up…";
  }
}

export function stepState(step, progress) {
  const at = ORDER.indexOf(progress.phase);
  const me = ORDER.indexOf(step.id);
  if (progress.mode === "rules" && step.who === "ai") return "skipped";
  if (at < 0) return "pending";
  return me < at ? "done" : me === at ? "active" : "pending";
}

const fmtTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const fmtNum = (n) => Number(n || 0).toLocaleString("en-US");

export function AnalysisProgress({ progress }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const p = progress || { phase: "queued", mode: "rules", model: "", calls: 0, tokens_in: 0, tokens_out: 0, episodes: 0 };
  const tiles = Math.max(10, Math.min(p.episodes || 12, 24));
  const usingAi = p.mode === "model";
  const title = phaseTitle(p);
  return html`<div class="aprog" data-phase=${p.phase} data-mode=${p.mode}>
    <div class="aprog-head" role="status" aria-live="polite">
      <span class="aprog-pulse" aria-hidden="true"></span>
      <div><b>${title}</b>
        <div class="muted small">${usingAi && p.model ? `Model: ${p.model} · ` : ""}${fmtTime(elapsed)} elapsed</div></div>
    </div>

    <div class="aprog-film" aria-hidden="true">
      <div class="aprog-strip">
        ${Array.from({ length: tiles }, (_, i) => html`<span key=${i} class="aprog-frame" style=${{ animationDelay: `${i * 0.12}s` }}></span>`)}
      </div>
      <div class="aprog-window">
        <span class="aprog-hook">hook</span>
        <span class="aprog-bar"></span>
        <span class="aprog-cliff">cliffhanger</span>
      </div>
    </div>

    <ol class="aprog-steps">
      ${STEPS.map((s) => {
        const st = stepState(s, p);
        return html`<li key=${s.id} class=${"aprog-step " + st}>
          <span class="aprog-mark" aria-hidden="true">${st === "done" ? "✓" : st === "skipped" ? "–" : ""}</span>
          <span class="aprog-label">${s.label}</span>
          <span class="aprog-who">${st === "skipped" ? "rules only" : s.who === "ai" ? (p.model ? providerName(p.model) : "AI") : "code"}</span>
        </li>`;
      })}
    </ol>

    <dl class="aprog-stats">
      <div><dt>Episodes read</dt><dd>${p.episodes ? fmtNum(p.episodes) : "–"}</dd></div>
      ${usingAi
        ? html`<div><dt>Model calls</dt><dd>${fmtNum(p.calls)}</dd></div>
               <div><dt>Tokens in / out</dt><dd>${fmtNum(p.tokens_in)} / ${fmtNum(p.tokens_out)}</dd></div>`
        : html`<div><dt>Model calls</dt><dd>0 (rules only)</dd></div>`}
    </dl>
    <p class="muted small aprog-note">${usingAi
      ? "Only the model's scores come back to this page. Your files stay in a private run, and the server's API key never leaves the server."
      : "Rules only: ranked by timing and keyword rules, no AI model is called."}</p>
  </div>`;
}
