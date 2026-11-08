import { html } from "../ui.js";
import { offsetInPromo } from "../format.js";
export function Beat({ label, beat, segments, hookRule }) {
  if (!beat) return html`<section class=${hookRule ? "beat beat-hook" : "beat beat-cliffhanger"}><h3>${label}</h3><p class="muted">Not identified.</p></section>`;
  const off = offsetInPromo(beat, segments);
  const late = hookRule && off != null && off > 3;
  return html`<section class=${hookRule ? "beat beat-hook" : "beat beat-cliffhanger"}>
    <h3>${label}</h3>
    <div class="beat-meta">
      Ep ${beat.episode} · ${beat.ts}
      ${off != null ? html` · <span class=${late ? "warn-text" : ""}>${off.toFixed(1)}s into the promo${late ? " (hook should land in the first 3s)" : ""}</span>` : null}
    </div>
    <blockquote>${beat.speaker ? html`<b>${beat.speaker}: </b>` : null}${beat.text}</blockquote>
  </section>`;
}

