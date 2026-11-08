import { html } from "../ui.js";
import { dur } from "../format.js";
export function CutTable({ p, entry, fb, review }) {
  const segs = entry ? entry.segments : p.segments;
  const can = !!(fb && fb.can_nudge) && !review.busy;
  const why = fb && !fb.can_nudge ? fb.nudge_reason : "";
  const N = ({ i, edge }) => html`<span class="nudge">
    <button type="button" class="nbtn" disabled=${!can} title=${why || "Move 0.5 s earlier"} aria-label=${`${edge} of segment ${i + 1}, 0.5 seconds earlier`}
      onClick=${() => review.nudge(i, edge, -0.5)}>\u22120.5</button>
    <button type="button" class="nbtn" disabled=${!can} title=${why || "Move 0.5 s later"} aria-label=${`${edge} of segment ${i + 1}, 0.5 seconds later`}
      onClick=${() => review.nudge(i, edge, 0.5)}>+0.5</button>
  </span>`;
  const err = review.nudgeErr && review.nudgeErr.promo === p.id ? review.nudgeErr : null;
  return html`<div>
    <table class="cuts">
      <thead><tr><th>#</th><th>Episode</th><th>Start</th><th>End</th><th>Length</th></tr></thead>
      <tbody>${segs.map((s, i) => html`<tr key=${i}>
        <td>${i + 1}</td><td>${s.episode}</td>
        <td><span class="mono">${s.start_ts}</span> <${N} i=${i} edge="start" /></td>
        <td><span class="mono">${s.end_ts}</span> <${N} i=${i} edge="end" /></td>
        <td>${dur(s.end_s - s.start_s)}</td>
      </tr>`)}</tbody>
    </table>
    ${entry && entry.nudged
      ? html`<p class="edited"><span class="tag">edited by you</span> The tool proposed ${entry.original.map((s) => `Ep ${s.episode} ${s.start_ts}\u2013${s.end_ts}`).join(" + ")}.
          <button type="button" class="btn small" disabled=${review.busy} onClick=${review.reset}>Reset cut</button></p>`
      : null}
    ${err
      ? html`<div class="nudge-err" role="alert">${err.error}
          ${(err.suggestions || []).length
            ? html`<div class="sugg">Nearest allowed cut points:
                ${err.suggestions.map((x) => html`<button type="button" key=${x.t} class="btn small" disabled=${review.busy}
                  onClick=${() => review.nudge(err.segment, err.edge, null, x.t)}>Use ${x.ts}</button>`)}</div>`
            : null}</div>`
      : null}
    ${why ? html`<p class="muted small">Cut nudges are off: ${why}</p>` : null}
  </div>`;
}

