import { html } from "../ui.js";
import { dur, fmt, flagLabel } from "../format.js";
import { ReviewPanel } from "./ReviewPanel.js";
import { CopyButton } from "./CopyButton.js";
import { CutTable } from "./CutTable.js";
import { Beat } from "./Beat.js";
import { Bars } from "./Bars.js";
export function Detail({ p, detailRef, fb, review, reviewable, position, count, onPrevious, onNext }) {
  const entry = fb && fb.entries[p.id];
  const segs = entry ? entry.segments : p.segments;
  const segText = segs.map((s) => `Ep ${s.episode}  ${s.start_ts} - ${s.end_ts}`).join("\n");
  const tags = [
    ...(p.risks || []).map((r) => ({ t: r.startsWith("needs context:") ? r : flagLabel(r), warn: true })),
    ...(p.flags || []).map((r) => ({ t: flagLabel(r), warn: true })),
    ...(p.end_refined ? [{ t: `end tightened (was ${p.original_end_ts}, ${dur(p.original_duration_s)})` }] : []),
    ...(p.ends_at_episode_end ? [{ t: "ends at the episode's end" }] : []),
  ];
  return html`<article class="panel detail" ref=${detailRef} tabIndex="-1" aria-labelledby="detail-title">
    <div class="candidate-toolbar"><span>SHORTLIST <span class="toolbar-divider">/</span> Candidate ${position + 1} of ${count}</span><div><button type="button" class="candidate-step" aria-label="Previous candidate" disabled=${position <= 0} onClick=${onPrevious}>←</button><button type="button" class="candidate-step" aria-label="Next candidate" disabled=${position >= count - 1} onClick=${onNext}>→</button></div></div>
    <header class="detail-head">
      <div><div class="eyebrow">PROMO ${String(p.rank).padStart(2, "0")}</div>
        <h2 id="detail-title">#${p.rank} <span class="detail-episodes">Episode ${[...new Set(segs.map((s) => s.episode))].join(" + ")}</span></h2>
        <div class="candidate-meta"><span>${p.kind === "single" ? "Single clip" : "Stitched promo"}</span><span>${dur(segs.reduce((sum, s) => sum + s.end_s - s.start_s, 0))}</span><span>${fmt(p.confidence * 10)}/10 confidence</span></div>
      </div>
      <div class="score-card" style=${{ "--pct": Math.max(0, Math.min(10, p.final_score ?? 0)) * 10 }} title="Final score (0–10)"><span class="metric-label">PROMO SCORE</span><div class="score-big">${fmt(p.final_score)}<small> / 10</small></div></div>
    </header>
    <div class="detail-main">
      <div class="section-intro"><span class="eyebrow">THE STORY</span><span>Source-backed moments</span></div>
      <div class="story-beats" data-section="story"><${Beat} label="Opening hook" beat=${p.hook} segments=${segs} hookRule=${true} /><${Beat} label="Closing cliffhanger" beat=${p.cliffhanger} segments=${segs} /></div>
      <section class="reason-card"><h3>Why it works</h3><p>${p.why || html`<span class="muted">No reason given.</span>`}</p>
        ${p.post_first_because ? html`<p class="post-reason">${p.post_first_because}</p>` : null}
      </section>
      <section class="cut-section" data-section="cuts">
        <div class="row-between"><div><h3>Assembly & timing</h3><p class="section-description">Fine-tune the source cuts in half-second steps.</p></div><${CopyButton} text=${segText} label="Copy timestamps" /></div>
        <div class="timeline" role="img" aria-label=${`Promo assembled from ${segs.length} cut${segs.length > 1 ? "s" : ""}: ${segs.map((s) => dur(s.end_s - s.start_s)).join(" + ")}`}>${segs.map((s, i) => html`<span key=${i} style=${{ flex: Math.max(1, s.end_s - s.start_s) }}>${dur(s.end_s - s.start_s)}</span>`)}</div>
        ${reviewable && fb ? html`<${CutTable} p=${p} entry=${entry} fb=${fb} review=${review} />` : html`<table class="cuts">
          <thead><tr><th>#</th><th>Episode</th><th>Start</th><th>End</th><th>Length</th></tr></thead>
          <tbody>${p.segments.map((s, i) => html`<tr key=${i}><td>${i + 1}</td><td>${s.episode}</td><td class="mono">${s.start_ts}</td><td class="mono">${s.end_ts}</td><td>${dur(s.end_s - s.start_s)}</td></tr>`)}</tbody>
        </table>`}
      </section>
    </div>
    <aside class="detail-inspector" aria-label="Editorial review and quality">
      <${ReviewPanel} p=${p} entry=${entry} review=${review} enabled=${reviewable && !!fb} />
      <section class="score-section" data-section="scores"><h3>Score breakdown</h3><${Bars} scores=${p.scores || {}} /></section>
      <section class="checks"><h3>Before you post</h3>${tags.length ? html`<ul class="tags">${tags.map((x, i) => html`<li key=${i} class=${"tag" + (x.warn ? " warn" : "")}>${x.t}</li>`)}</ul>` : html`<p class="clear-check">✓ No quality issues flagged</p>`}</section>
    </aside>
  </article>`;
}
