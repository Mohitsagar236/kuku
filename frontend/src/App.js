import { useState, useEffect, useRef, useCallback } from "react";
import { html } from "./ui.js";
import { getJSON, sendJSON } from "./api.js";
import { readHash, writeHash, hashFor } from "./navigation.js";
import { KIND, variantLabel, pct, demoScoring, isRulesOnly } from "./format.js";
import { Markdown } from "./Markdown.js";
import { CopyButton } from "./components/CopyButton.js";
import { State } from "./components/State.js";
import { Detail } from "./components/Detail.js";
import { PromoList } from "./components/PromoList.js";
import { AnalyzingScreen } from "./components/AnalyzingScreen.js";
import { Home } from "./components/Home.js";
import { MainNav } from "./components/Nav.js";
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
  const [screen, setScreen] = useState(init.s);
  const [job, setJob] = useState(init.job);
  const [reload, setReload] = useState(0);
  const [fb, setFb] = useState(null);
  const [fbErr, setFbErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState({});
  const [nudgeErr, setNudgeErr] = useState(null);
  const [caps, setCaps] = useState(null); // server capabilities; null until known or unsupported
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
    const own = activeRun.current.startsWith("upload-") ? `?extra=${encodeURIComponent(activeRun.current)}` : "";
    getJSON(`/api/runs${own}`, ac.signal)
      .then((r) => {
        setRuns(r);
        setRunId((cur) => (cur && r.some((x) => x.id === cur) ? cur : (r.find((x) => x.kind === "full" && !x.partial) || r[0] || {}).id || ""));
      })
      .catch((e) => e.name !== "AbortError" && setRunsErr(e.message));
    return () => ac.abort();
  }, [reload]);

  useEffect(() => {
    getJSON("/api/capabilities").then((c) => setCaps(c.analyze ? c : null)).catch(() => setCaps(null));
  }, []);

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

  const tab = screen === "report" ? "report" : "promos"; // existing logic keys off the tab
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
    writeHash({ s: screen, run: runId, v: variant, p: selected && selected.id, job });
  }, [screen, runId, variant, selected, job]);

  useEffect(() => { window.scrollTo(0, 0); }, [screen]);

  // The URL is the single source of truth: back/forward, an edited address bar or a pasted
  // link in an open tab all fire "hashchange", and the screen follows the hash.
  const latest = useRef({});
  latest.current = { runs, run, runId, variant, screen, job, selectedId: selected && selected.id };
  useEffect(() => {
    const onHashChange = () => {
      const h = readHash();
      const L = latest.current;
      setScreen(h.s);
      setJob(h.job);
      if (h.s === "home" || h.s === "analyzing") return; // nothing else to sync
      if (h.run && L.runs && !L.runs.some((x) => x.id === h.run)) {
        // Unknown run: keep what is on screen and put a truthful URL back.
        writeHash({ s: L.screen, run: L.runId, v: L.variant, p: L.selectedId, job: L.job });
        return;
      }
      if (h.run && h.run !== L.runId) {
        wantPromo.current = h.p; // the run-load effect applies v and p once the run arrives
        setVariant(h.v);
        setRunId(h.run);
        return;
      }
      if (!L.run) { // same run, still loading: the load effect reads the hash when it finishes
        wantPromo.current = h.p;
        return;
      }
      const nextVariant = L.run.variants[h.v] ? h.v : "main";
      const list = (L.run.variants[nextVariant] || {}).promos || [];
      const idx = h.p ? list.findIndex((p) => p.id === h.p) : -1;
      if (nextVariant !== L.variant) setVariant(nextVariant);
      if (h.s === "report") return; // the report has no promo in its address: keep the selection
      setQuery("");
      setReviewFilter("all"); // a hidden promo would otherwise leave the URL and screen apart
      setSel(Math.max(0, idx));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

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

  const dl = (name) => `/api/file?id=${encodeURIComponent(runId)}&name=${name}`;
  const hasRun = !!runId && !!cur;
  const links = {
    home: "#",
    shortlist: hashFor({ s: "shortlist", run: runId, v: variant, p: selectedId }),
    report: hashFor({ s: "report", run: runId, v: variant }),
  };
  const go = (next) => { location.hash = next; };
  const submitted = (token) => { setJob(token); setScreen("analyzing"); };
  const finished = (r) => {
    setJob(""); setVariant("main"); wantPromo.current = "";
    setRunId(r.id); setReload((n) => n + 1); setScreen("shortlist");
  };

  const pageHead = (eyebrow) => html`<section class="page-head">
    <div><div class="eyebrow"><span class="hero-line" aria-hidden="true"></span> ${eyebrow}</div><h2>${cur?.show || "Your shortlist"}</h2></div>
    <div class="page-tools">
      ${screen === "shortlist" && run && Object.keys(run.variants).length > 1
        ? html`<label class="field"><span>Ranking</span>
            <select aria-label="Ranking" value=${variant} onChange=${(e) => { setVariant(e.target.value); setSel(0); }}>
              ${Object.keys(run.variants).map((v) => html`<option key=${v} value=${v}>${variantLabel(v)}</option>`)}
            </select></label>`
        : null}
      <div class="run-meta"><span class="live-dot"></span>${KIND[cur?.kind] || "Analysis"}<span class="meta-divider">/</span>${promos.length} candidates</div>
      ${run && run.downloads && run.downloads.length
        ? html`<div class="downloads">${run.downloads.map((n) => html`<a key=${n} class="btn small" href=${dl(n)} download>${n.endsWith(".csv") ? "Download CSV" : "Download ranking"}</a>`)}</div>`
        : null}
    </div>
  </section>`;

  const noResults = html`<${State} title="No results yet.">
    <p>Upload a screenplay on the Home screen, or run the tool on a show folder and reload.</p>
    <a class="btn primary" href="#">Go to Home</a>
  <//>`;

  const shortlistScreen = !hasRun ? noResults : html`<div>
    ${pageHead("SHORTLIST")}
    ${isDemo ? html`<div class="banner info demo-notice" role="note"><strong>Interactive demo</strong> · Fictional sample story. ${demoScoring(cur)}. Reviews are shared and reset when the service restarts.</div>` : null}
    ${cur && isRulesOnly(cur.model)
      ? html`<div class="banner info" role="note">Rules-only ranking: promos are ranked by timing and keyword rules; no AI model judged them. Run it with a model for better hook, cliffhanger and clarity judgement.</div>`
      : null}
    ${cur && cur.partial && !isRulesOnly(cur.model)
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
        : html`<main class=${"grid" + (loading ? " dim" : "")} aria-busy=${loading}>
            ${promos.length
              ? html`<${PromoList} promos=${promos} visibleIndices=${visibleIndices} sel=${sel} onSelect=${select} entries=${entries} query=${query} onQuery=${setQuery} filter=${reviewFilter} onFilter=${setReviewFilter} filterEnabled=${reviewable && !!fb} />`
              : null}
            ${selected
              ? html`<${Detail} p=${selected} detailRef=${detailRef} fb=${fb} review=${review} reviewable=${reviewable} position=${visibleIndices.indexOf(sel)} count=${visibleIndices.length} onPrevious=${() => select(visibleIndices[visibleIndices.indexOf(sel) - 1], false)} onNext=${() => select(visibleIndices[visibleIndices.indexOf(sel) + 1], false)} />`
              : promos.length ? html`<${State} title="No matching promos."><p>Try another search or clear your filters to see the full shortlist.</p><button type="button" class="btn" onClick=${() => { setQuery(""); setReviewFilter("all"); }}>Clear filters</button><//>` : html`<${State} title="No promos in this ranking.">The tool found no candidate that passed its hook and cliffhanger gates.<//>`}
          </main>`}
  </div>`;

  const reportScreen = !hasRun ? noResults : html`<div>
    ${pageHead("QUALITY REPORT")}
    ${runErr
      ? html`<${State} kind="err" title="Couldn't load this run."><p>${runErr}</p>
          <button type="button" class="btn" onClick=${() => setReload((n) => n + 1)}>Try again</button><//>`
      : !run
        ? html`<${State}>Loading run…<//>`
        : html`<main class=${"report" + (loading ? " dim" : "")}><article class="panel">
            ${run.report ? html`<${Markdown} src=${run.report} />` : html`<p class="muted">This run has no quality report (rules baselines don't write one).</p>`}
          </article></main>`}
  </div>`;

  return html`<div class="app">
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">P<span>▶</span></span>
        <div><h1>Promo Finder</h1><span class="brand-caption">THE EDITOR WORKSPACE</span></div>
      </div>
      <${MainNav} screen=${screen} links=${links} hasRun=${hasRun} />
      <span class="local-badge"><span></span> ${isDemo ? "Public demo" : "Local workspace"}</span>
    </header>

    ${screen === "home"
      ? html`<${Home} caps=${caps} cur=${cur} isDemo=${isDemo} links=${links} onSubmitted=${submitted} />`
      : screen === "analyzing"
        ? html`<main class="home"><${AnalyzingScreen} job=${job} onDone=${finished} onBack=${() => go("#")} /></main>`
        : screen === "report" ? reportScreen : shortlistScreen}

    <footer class="foot muted"><span>${isDemo ? "Sample workspace · Temporary shared reviews" : "Private workspace · Reviews saved on this device"}</span>${screen === "shortlist" ? html`<span><kbd>↑</kbd> <kbd>↓</kbd> or <kbd>j</kbd> <kbd>k</kbd> to navigate</span>` : null}${fb?.refresh_command ? html`<details class="report-tools"><summary>Report tools</summary><${CopyButton} text=${fb.refresh_command} label="Copy report-refresh command" /></details>` : null}</footer>
  </div>`;
}
