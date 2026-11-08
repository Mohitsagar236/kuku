import { html } from "../ui.js";
import { useState, useEffect, useRef } from "react";
import { VERDICTS } from "../format.js";
export function ReviewPanel({ p, entry, review, enabled }) {
  const savedNote = entry?.note || "";
  const [note, setNote] = useState(savedNote);
  const previous = useRef({ id: p.id, saved: savedNote });
  useEffect(() => {
    const old = previous.current;
    setNote((draft) => old.id !== p.id || draft === old.saved ? savedNote : draft);
    previous.current = { id: p.id, saved: savedNote };
  }, [p.id, savedNote]);
  if (!enabled)
    return html`<section><h3>Your review</h3><p class="muted">Reviews are saved on the main ranking only.</p></section>`;
  const cur = entry && entry.verdict;
  return html`<section class="review">
    <h3>Your review</h3>
    <div class="verdicts" role="group" aria-label="Your verdict on this promo">
      ${VERDICTS.map(([k, label, icon]) => html`<button type="button" key=${k}
        class=${"vbtn " + k + (cur === k ? " on" : "")} aria-pressed=${cur === k} aria-busy=${review.busy}
        onClick=${() => review.setVerdict(cur === k ? "clear" : k, note)}><span aria-hidden="true">${icon}</span> ${label}</button>`)}
    </div>
    <label class="notelabel" for="review-note">Editorial note <span class="muted">· optional</span></label>
    <textarea id="review-note" placeholder="What works? What would you change?" rows="2" maxLength="1000" value=${note}
      onInput=${(e) => setNote(e.target.value)}
      onBlur=${() => note.trim() !== ((entry && entry.note) || "") && review.saveNote(note)}></textarea>
    <div class=${"status " + (review.status.kind || "")} role="status" aria-live="polite">${review.status.text || ""}</div>
  </section>`;
}

