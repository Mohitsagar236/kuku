export const DIMS = [
  ["hook", "Hook"],
  ["cliffhanger", "Cliffhanger"],
  ["clarity", "Stands alone"],
  ["intensity", "Intensity"],
  ["spoiler_risk", "Spoiler risk"],
];
export const KIND = { full: "Full run", smoke: "Smoke test", baseline: "Rules baseline" };
export const VARIANT = {
  main: "Main ranking",
  ablation_literal_cliffhanger: "Ablation: without literal cliffhanger cues",
  ablation_episode_end_prior: "Ablation: without episode-end prior",
};
export const FLAG = {
  starts_mid_scene: "starts mid-scene",
  weak_ending: "weak ending",
  many_speakers: "many speakers",
  spoiler_risk_high: "high spoiler risk",
  dead_air: "dead air",
  continuity_dependent: "needs earlier episodes",
  stitched: "stitched from several moments",
  rule_fallback_main: "scored by rules, not the LLM",
  rule_fallback_clarity: "clarity scored by rules, not the LLM",
};
export const fmt = (n) => (n == null || Number.isNaN(Number(n)) ? "–" : Number(n).toFixed(1));
export const dur = (s) => (s == null ? "–" : `${Math.round(s)}s`);
export const variantLabel = (v) => VARIANT[v] || v.replace(/_/g, " ");
export const flagLabel = (f) => FLAG[f] || f.replace(/_/g, " ");
export const when = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "unknown time");

// "01:59.60" / "1:02:03.5" -> seconds
export function tsToSec(ts) {
  if (!ts) return null;
  const parts = String(ts).split(":").map(Number);
  if (parts.some(Number.isNaN)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

// Where the beat lands inside the assembled promo (seconds from the promo's first frame).
export function offsetInPromo(beat, segments) {
  const t = tsToSec(beat && beat.ts);
  if (t == null) return null;
  let before = 0;
  for (const s of segments) {
    if (s.episode === beat.episode && t >= s.start_s - 0.01 && t <= s.end_s + 0.01) {
      return Math.max(0, before + t - s.start_s);
    }
    before += s.end_s - s.start_s;
  }
  return null;
}

export const VERDICTS = [
  ["approve", "Approve", "\u2713"],
  ["would_post", "Would post", "\u2605"],
  ["reject", "Reject", "\u2715"],
];
export const VERDICT_WORD = { approve: "approved", would_post: "would post", reject: "rejected" };
export const pct = (n) => (n == null ? "\u2013" : Number(n).toFixed(2));


/** Demo banner wording. "Simulated" only when the run really used the mock model. */
export function demoScoring(run) {
  const model = (run && run.model) || "";
  if (!model || model.startsWith("mock")) return "simulated scores";
  const day = run.generated_at ? run.generated_at.slice(0, 10) : "an unknown date";
  return `Scored by ${model} on ${day}`;
}
