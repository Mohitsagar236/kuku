import { html } from "../ui.js";
import { DIMS, fmt } from "../format.js";
export function Bars({ scores }) {
  return html`<div class="bars" role="list">
    ${DIMS.map(([d, label]) => {
      const inv = d === "spoiler_risk";
      const v = scores[d];
      const rule = scores[`rule_${d}`];
      const llm = scores[`llm_${d}`];
      return html`<div class="bar-row" role="listitem" key=${d}>
        <span class="bar-label">${label}${inv ? html`<small> lower is better</small>` : null}</span>
        <div class="track" aria-hidden="true"><div class=${"fill" + (inv ? " inv" : "")} style=${{ width: `${Math.max(0, Math.min(10, v ?? 0)) * 10}%` }}></div></div>
        <span class="bar-val">${fmt(v)}</span>
        ${rule != null || llm != null
          ? html`<span class="bar-src">rules ${fmt(rule)} · LLM ${fmt(llm)}</span>`
          : null}
      </div>`;
    })}
  </div>`;
}

