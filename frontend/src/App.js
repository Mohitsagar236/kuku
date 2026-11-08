import { useState, useEffect, useRef, useCallback } from "react";
import { html } from "./ui.js";
import { getJSON, sendJSON } from "./api.js";
import { readHash, writeHash } from "./navigation.js";
import { KIND, when, variantLabel, pct } from "./format.js";
import { Markdown } from "./Markdown.js";
import { CopyButton } from "./components/CopyButton.js";
import { State } from "./components/State.js";
import { Detail } from "./components/Detail.js";
import { PromoList } from "./components/PromoList.js";
export function App() {
  const init = useRef(readHash()).current;
  const [runs, setRuns] = useState(null);
  const [runsErr, setRunsErr] = useState("");
  const [runId, setRunId] = useState(init.run);
  const [run, setRun] = useState(null);
  const [runErr, setRunErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [variant, setVariant] = useState(init.v);
  const [sel, setSel] = useState(0);
  const [query, setQuery] = useState("");
  const [reviewFilter, setReviewFilter] = useState("all");
  const [tab, setTab] = useState(init.tab === "report" ? "report" : "promos");
  const [reload, setReload] = useState(0);
  const [fb, setFb] = useState(null);
  const [fbErr, setFbErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState({});
  const [nudgeErr, setNudgeErr] = useState(null);
  const wantPromo = useRef(init.p);
  const detailRef = useRef(null);
  const activeRun = useRef(runId);
  activeRun.current = runId;
  const activePromo = useRef("");
  const writeQueue = useRef(Promise.resolve());
  const pendingWrites = useRef(0);

  useEffect(() => {
    const ac = new AbortController();
    setRunsErr("");
    getJSON("/api/runs", ac.signal)
      .then((r) => {
        setRuns(r);
        setRunId((cur) => (cur && r.some((x) => x.id === cur) ? cur : (r.find((x) => x.kind === "full" && !x.partial) || r[0] || {}).id || ""));
      })
      .catch((e) => e.name !== "AbortError" && setRunsErr(e.message));
    return () => ac.abort();
  }, [reload]);

  useEffect(() => {
    if (!runId) return;
    const ac = new AbortController();
    setLoading(true);
    setRun(null);
    setRunErr("");
    getJSON(`/api/run?id=${encodeURIComponent(runId)}`, ac.signal)
      .then((r) => {
        setRun(r);
        const requestedVariant = readHash().v;
        const nextVariant = r.variants[requestedVariant] ? requestedVariant : "main";
        setVariant(nextVariant);
        const promos = (r.variants[nextVariant] || {}).promos || [];
        const idx = wantPromo.current ? promos.findIndex((p) => p.id === wantPromo.current) : -1;
        wantPromo.current = "";
        setSel(Math.max(0, idx));
        setLoading(false);
      })
      .catch((e) => {
        if (e.name === "AbortError") return;
        setRun(null);
        setRunErr(e.message);
        setLoading(false);
      });
    return () => ac.abort();
  }, [runId, reload]);

  useEffect(() => {
    if (!runId) return;
    const ac = new AbortController();
    setFb(null);
    setFbErr("");
    getJSON(`/api/feedback?id=${encodeURIComponent(runId)}`, ac.signal)
      .then(setFb)
      .catch((e) => e.name !== "AbortError" && setFbErr(e.message));
    return () => ac.abort();
  }, [runId, reload]);

  const data = run && (run.variants[variant] || run.variants.main);
  const promos = (data && data.promos) || [];
  const cur = runs && runs.find((r) => r.id === runId);
  const entries = variant === "main" ? fb?.entries || {} : {};
  const normalizedQuery = query.trim().toLowerCase();
  const visibleIndices = promos.flatMap((p, i) => {
    const verdict = entries[p.id]?.verdict;
    const matchesReview = reviewFilter === "all" || (reviewFilter === "unreviewed" ? !verdict : verdict === reviewFilter);
    const searchable = [p.rank, p.hook?.text, p.cliffhanger?.text, p.why, ...p.segments.map((segment) => `episode ${segment.episode} ep ${segment.episode}`)].join(" ").toLowerCase();
    return matchesReview && (!normalizedQuery || searchable.includes(normalizedQuery)) ? [i] : [];
  });
  const visibleKey = visibleIndices.join(",");
  const reviewedCount = promos.filter((p) => entries[p.id]?.verdict).length;
  const isDemo = cur?.model === "mock/render-demo" || cur?.show === "The Quarterly Close - Sample Story";
  const selected = visibleIndices.length ? promos[Math.min(sel, promos.length - 1)] : null;

  useEffect(() => {
    setQuery("");
    setReviewFilter("all");
  }, [runId, variant]);

  useEffect(() => {
    const indices = visibleKey ? visibleKey.split(",").map(Number) : [];
    if (indices.length) setSel((current) => indices.includes(current) ? current : indices.find((index) => index > current) ?? indices[0]);
  }, [visibleKey]);
  activePromo.current = selected?.id || "";
  const selectedId = selected?.id;

  useEffect(() => {
    writeHash({ run: runId, v: variant, p: selected && selected.id, tab });
  }, [runId, variant, selected, tab]);

  useEffect(() => {
    setStatus({});
    setNudgeErr(null);
  }, [selectedId]);

  // Serialize writes so blur-save and a following verdict/cut action both reach the server.
  const act = useCallback(
    (path, body, okText, ctx = {}) => {
      const targetRun = runId;
      pendingWrites.current += 1;
      setBusy(true);
      const perform = async () => {
        if (activeRun.current === targetRun) {
          setNudgeErr(null);
          setStatus({});
        }
        const r = await sendJSON(`${path}?id=${encodeURIComponent(targetRun)}`, body);
        if (activeRun.current !== targetRun) return;
        if (r.ok) {
          setFb(r.data);
          if (activePromo.current === body.promo_id) {
            setStatus({ kind: "ok", text: `${okText}. Changes saved.` });
          }
        } else if (activePromo.current === body.promo_id) {
          if (r.status === 422) {
            setNudgeErr({ ...r.data, promo: body.promo_id, segment: ctx.segment, edge: ctx.edge });
          } else {
            setStatus({ kind: "err", text: r.data?.error || `Request failed (${r.status})` });
          }
        }
      };
      const pending = writeQueue.current.then(perform).catch(() => {
        if (activeRun.current === targetRun && activePromo.current === body.promo_id) {
          setStatus({ kind: "err", text: "Could not save the change. Please try again." });
        }
      }).finally(() => {
        pendingWrites.current -= 1;
        setBusy(pendingWrites.current > 0);
      });
      writeQueue.current = pending;
      return pending;
    },
    [runId]
  );
  const review = {
    busy,
    status,
    nudgeErr,
    setVerdict: (v, note) => selected && act("/api/review", { promo_id: selected.id, verdict: v, ...(typeof note === "string" ? { note } : {}) }, v === "clear" ? "Cleared" : "Saved", {}),
    saveNote: (n) => selected && act("/api/review", { promo_id: selected.id, note: n }, "Note saved", {}),
    nudge: (i, edge, delta, to) =>
      selected && act("/api/nudge", { promo_id: selected.id, segment: i, edge, ...(to != null ? { to } : { delta }) }, "Cut updated", { segment: i, edge }),
    reset: () => selected && act("/api/nudge", { promo_id: selected.id, reset: true }, "Cut reset", {}),
  };
  const reviewable = tab === "promos" && variant === "main";

  const select = useCallback((i, fromClick) => {
    setSel(i);
    if (fromClick && window.matchMedia("(max-width: 760px)").matches && detailRef.current) {
      requestAnimationFrame(() => detailRef.current.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }, []);

  // j/k or arrow keys move through the ranking (ignored while a form control has focus)
  useEffect(() => {
    const visibleIndices = visibleKey ? visibleKey.split(",").map(Number) : [];
    const onKey = (e) => {
      if (tab !== "promos" || !visibleIndices.length || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target.closest && e.target.closest("select, input, textarea")) return;
      const down = e.key === "ArrowDown" || e.key === "j";
      const up = e.key === "ArrowUp" || e.key === "k";
      if (!down && !up) return;
      e.preventDefault();
      setSel((s) => visibleIndices[Math.max(0, Math.min(visibleIndices.length - 1, visibleIndices.indexOf(s) + (down ? 1 : -1)))]);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [tab, visibleKey]);

  if (runsErr)
    return html`<${State} kind="err" title="Couldn't load runs.">
      <p>${runsErr}</p><button type="button" class="btn" onClick=${() => setReload((n) => n + 1)}>Try again</button>
    <//>`;
  if (!runs) return html`<${State}>Loading runs…<//>`;
  if (!runs.length)
    return html`<${State} title="No results yet.">
      <p>Run the tool on a show folder first, then reload this page:</p>
      <pre>make run-rules SHOW_DIR="path/to/show"   # free, no API key\nmake run SHOW_DIR="path/to/show"         # with the LLM in .env</pre>
    <//>`;

  const shows = [...new Set(runs.map((r) => r.show))];
  const dl = (name) => `/api/file?id=${encodeURIComponent(runId)}&name=${name}`;

  return html`<div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">P<span>▶</span></span>
        <div><h1>Promo Finder</h1><span class="brand-caption">THE EDITOR WORKSPACE</span></div>
      </div><span class="local-badge"><span></span> ${isDemo ? "Public demo" : "Local workspace"}</span>
      <div class="workspace-controls">
      <label class="field">
        <span>Run</span>
        <select aria-label="Run" value=${runId} onChange=${(e) => { setRunId(e.target.value); setVariant("main"); }}>
          ${shows.map((s) => html`<optgroup key=${s} label=${s}>
            ${runs.filter((r) => r.show === s).map((r) => html`<option key=${r.id} value=${r.id}>
              ${KIND[r.kind] || r.kind} · ${when(r.generated_at)} · ${r.model || "unknown model"} · ${r.count} promos${r.partial ? " · partial" : ""}
            </option>`)}
          </optgroup>`)}
        </select>
      </label>
      ${run && Object.keys(run.variants).length > 1
        ? html`<label class="field">
            <span>Ranking</span>
            <select aria-label="Ranking" value=${variant} onChange=${(e) => { setVariant(e.target.value); setSel(0); }}>
              ${Object.keys(run.variants).map((v) => html`<option key=${v} value=${v}>${variantLabel(v)}</option>`)}
            </select>
          </label>`
        : null}
      <div class="tabs" role="tablist" aria-label="View">
        <button type="button" role="tab" aria-selected=${tab === "promos"} class=${tab === "promos" ? "on" : ""} onClick=${() => setTab("promos")}>Promos</button>
        <button type="button" role="tab" aria-selected=${tab === "report"} class=${tab === "report" ? "on" : ""} onClick=${() => setTab("report")}>Quality report</button>
      </div>
      ${run && run.downloads && run.downloads.length
        ? html`<div class="downloads">${run.downloads.map((n) => html`<a key=${n} class="btn small" href=${dl(n)} download>${n.endsWith(".csv") ? "Download CSV" : "Download ranking"}</a>`)}</div>`
        : null}
      </div>
    </header>

    <div class="workspace-heading"><div><div class="eyebrow"><span class="hero-line" aria-hidden="true"></span> YOUR NEXT GREAT PROMO STARTS HERE</div><h2>${cur?.show || "Your shortlist"}</h2><p>Review the shortlist. Shape the next standout promo.</p></div><div class="hero-aside"><div class="hero-format" aria-hidden="true"><span>20–60</span><small>SECONDS. ALL IMPACT.</small></div><div class="run-meta"><span class="live-dot"></span>${KIND[cur?.kind] || "Analysis"}<span class="meta-divider">/</span>${promos.length} candidates</div></div></div>

    ${isDemo ? html`<div class="banner info demo-notice" role="note"><strong>Interactive demo</strong> · Fictional sample story and simulated scores. Reviews are shared and reset when the service restarts.</div>` : null}

    ${cur && cur.partial
      ? html`<div class="banner" role="note">Partial run: LLM scoring stopped early (no key, quota or budget), so some promos are ranked on rule scores only.</div>`
      : null}
    ${variant !== "main" && run && run.variants[variant]
      ? html`<div class="banner info" role="note">You are viewing an ablation (a robustness check), not the ranking to post from.</div>`
      : null}

    ${fbErr ? html`<div class="banner err" role="alert">Editor feedback unavailable: ${fbErr}</div>` : null}
    ${fb && reviewable && !runErr && run
      ? html`<div class="reviewbar" role="status">
          <div class="review-progress"><span class="metric-icon" aria-hidden="true">◷</span><span class="metric-label">REVIEW PROGRESS</span><div><strong>${reviewedCount}</strong><span class="muted"> / ${promos.length} reviewed</span></div><div class="progress-track"><span style=${{ width: `${Math.min(100, reviewedCount / Math.max(1, promos.length) * 100)}%` }}></span></div></div>
          <div class="review-metric metric-approved"><span class="metric-icon" aria-hidden="true">✓</span><span class="metric-label">APPROVED</span><strong class="positive">${fb.summary.approved}</strong></div>
          <div class="review-metric metric-post"><span class="metric-icon" aria-hidden="true">✦</span><span class="metric-label">WOULD POST</span><strong>${fb.summary.would_post}</strong></div>
          <div class="review-metric metric-rejected"><span class="metric-icon" aria-hidden="true">×</span><span class="metric-label">REJECTED</span><strong>${fb.summary.rejected}</strong></div>
          <div class="review-quality" title="Unreviewed promos count as misses until reviewed"><span class="metric-label">PRECISION @ ${fb.summary.k}</span><strong>${pct(fb.summary.precision_at_k)}</strong><span class="muted small">${fb.summary.top_unreviewed} in top ${fb.summary.top} still to review</span></div>
        </div>`
      : null}

    ${runErr
      ? html`<${State} kind="err" title="Couldn't load this run."><p>${runErr}</p>
          <button type="button" class="btn" onClick=${() => setReload((n) => n + 1)}>Try again</button><//>`
      : !run
        ? html`<${State}>Loading run…<//>`
        : tab === "report"
          ? html`<main class=${"report" + (loading ? " dim" : "")}><article class="panel">
              ${run.report ? html`<${Markdown} src=${run.report} />` : html`<p class="muted">This run has no quality report (rules baselines don't write one).</p>`}
            </article></main>`
          : html`<main class=${"grid" + (loading ? " dim" : "")} aria-busy=${loading}>
              ${promos.length
                ? html`<${PromoList} promos=${promos} visibleIndices=${visibleIndices} sel=${sel} onSelect=${select} entries=${entries} query=${query} onQuery=${setQuery} filter=${reviewFilter} onFilter=${setReviewFilter} filterEnabled=${reviewable && !!fb} />`
                : null}
              ${selected
                ? html`<${Detail} p=${selected} detailRef=${detailRef} fb=${fb} review=${review} reviewable=${reviewable} position=${visibleIndices.indexOf(sel)} count=${visibleIndices.length} onPrevious=${() => select(visibleIndices[visibleIndices.indexOf(sel) - 1], false)} onNext=${() => select(visibleIndices[visibleIndices.indexOf(sel) + 1], false)} />`
                : promos.length ? html`<${State} title="No matching promos."><p>Try another search or clear your filters to see the full shortlist.</p><button type="button" class="btn" onClick=${() => { setQuery(""); setReviewFilter("all"); }}>Clear filters</button><//>` : html`<${State} title="No promos in this ranking.">The tool found no candidate that passed its hook and cliffhanger gates.<//>`}
            </main>`}
    <footer class="foot muted"><span>${isDemo ? "Sample workspace · Temporary shared reviews" : "Private workspace · Reviews saved on this device"}</span><span><kbd>↑</kbd> <kbd>↓</kbd> or <kbd>j</kbd> <kbd>k</kbd> to navigate</span>${fb?.refresh_command ? html`<details class="report-tools"><summary>Report tools</summary><${CopyButton} text=${fb.refresh_command} label="Copy report-refresh command" /></details>` : null}</footer>
  </div>`;
}

