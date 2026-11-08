import { html } from "../ui.js";
import { useRef, useEffect } from "react";
import { dur, fmt, VERDICTS, VERDICT_WORD } from "../format.js";
export function PromoList({ promos, visibleIndices, sel, onSelect, entries, query, onQuery, filter, onFilter, filterEnabled }) {
  const listRef = useRef(null);
  useEffect(() => {
    // Keep the selected row visible inside the list's own scroll box. Never scroll the page itself:
    // that used to push the header (and the upload button) out of view on first load.
    const list = listRef.current;
    const el = list && list.querySelector('[aria-current="true"]');
    if (!list || !el) return;
    const top = el.getBoundingClientRect().top - list.getBoundingClientRect().top + list.scrollTop;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (top + el.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = top + el.offsetHeight - list.clientHeight;
  }, [sel]);
  return html`<aside class="shortlist">
    <div class="shortlist-controls"><div class="list-heading"><span>Ranked shortlist</span><span class="count-badge">${visibleIndices.length} / ${promos.length}</span></div><p class="list-hint">Find your next standout moment</p>
    <label class="search-box"><span aria-hidden="true">⌕</span><input type="search" aria-label="Search promos" placeholder="Search dialogue or episode…" value=${query} onInput=${(e) => onQuery(e.target.value)} /></label>
    ${filterEnabled ? html`<label class="filter-field"><span>Review status</span><select aria-label="Filter by review status" value=${filter} onChange=${(e) => onFilter(e.target.value)}><option value="all">All candidates</option><option value="unreviewed">Awaiting review</option><option value="approve">Approved</option><option value="would_post">Would post</option><option value="reject">Rejected</option></select></label>` : null}
    </div><nav class="list" ref=${listRef} aria-label="Ranked promos">
    ${promos.map((p, i) => {
      if (!visibleIndices.includes(i)) return null;
      const warn = (p.flags || []).length > 0 || (p.risks || []).includes("weak_ending");
      return html`<button type="button" key=${p.id} class="item" aria-current=${i === sel ? "true" : "false"} onClick=${() => onSelect(i, true)}>
        <span class="top"><span><span class="rank">#${p.rank}</span> · Ep ${p.segments.map((s) => s.episode).filter((e, j, a) => a.indexOf(e) === j).join(", ")}</span><span class="score" title="Promo score out of 10">${fmt(p.final_score)}<i aria-hidden="true"><b style=${{ width: `${Math.max(0, Math.min(10, p.final_score ?? 0)) * 10}%` }}></b></i></span></span>
        <span class="item-preview">${p.hook?.text || p.why || "Explore this candidate"}</span><span class="sub">${(entries[p.id] ? entries[p.id].segments : p.segments)[0].start_ts} · ${dur((entries[p.id]?.segments || p.segments).reduce((sum, s) => sum + s.end_s - s.start_s, 0))}${p.segments.length > 1 ? ` · ${p.segments.length} parts` : ""}${warn ? html` · <span class="warn-text">check</span>` : null}${entries[p.id] && entries[p.id].verdict ? html` · <span class=${"vtag " + entries[p.id].verdict}>${VERDICTS.find((v) => v[0] === entries[p.id].verdict)[2]} ${VERDICT_WORD[entries[p.id].verdict]}</span>` : null}${entries[p.id] && entries[p.id].nudged ? html` · <span class="muted">edited</span>` : null}</span>
      </button>`;
    })}
  </nav>${!visibleIndices.length ? html`<p class="list-empty">No candidates match these filters.</p>` : null}</aside>`;
}

