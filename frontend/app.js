(function () {
  "use strict";
  const root = document.getElementById("root");
  if (!window.React || !window.ReactDOM || !window.htm) {
    root.innerHTML =
      '<div class="state err"><b>Could not load the UI libraries.</b>' +
      "<p>The viewer loads React from unpkg.com. Check your internet connection and reload.</p></div>";
    return;
  }

  const { useState, useEffect, useRef, useCallback } = React;
  const html = htm.bind(React.createElement);

  // ---------- formatting ----------
  const DIMS = [
    ["hook", "Hook"],
    ["cliffhanger", "Cliffhanger"],
    ["clarity", "Stands alone"],
    ["intensity", "Intensity"],
    ["spoiler_risk", "Spoiler risk"],
  ];
  const KIND = { full: "Full run", smoke: "Smoke test", baseline: "Rules baseline" };
  const VARIANT = {
    main: "Main ranking",
    ablation_literal_cliffhanger: "Ablation: without literal cliffhanger cues",
    ablation_episode_end_prior: "Ablation: without episode-end prior",
  };
  const FLAG = {
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
  const fmt = (n) => (n == null || Number.isNaN(Number(n)) ? "–" : Number(n).toFixed(1));
  const dur = (s) => (s == null ? "–" : `${Math.round(s)}s`);
  const variantLabel = (v) => VARIANT[v] || v.replace(/_/g, " ");
  const flagLabel = (f) => FLAG[f] || f.replace(/_/g, " ");
  const when = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "unknown time");

  // "01:59.60" / "1:02:03.5" -> seconds
  function tsToSec(ts) {
    if (!ts) return null;
    const parts = String(ts).split(":").map(Number);
    if (parts.some(Number.isNaN)) return null;
    return parts.reduce((acc, p) => acc * 60 + p, 0);
  }

  // Where the beat lands inside the assembled promo (seconds from the promo's first frame).
  function offsetInPromo(beat, segments) {
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

  // ---------- URL hash state: #run=...&v=...&p=...&tab=... ----------
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    return { run: h.get("run") || "", v: h.get("v") || "main", p: h.get("p") || "", tab: h.get("tab") || "promos" };
  }
  function writeHash(s) {
    const h = new URLSearchParams();
    if (s.run) h.set("run", s.run);
    if (s.v && s.v !== "main") h.set("v", s.v);
    if (s.p) h.set("p", s.p);
    if (s.tab && s.tab !== "promos") h.set("tab", s.tab);
    const next = "#" + h.toString();
    if (location.hash !== next) history.replaceState(null, "", next);
  }

  async function getJSON(url, signal) {
    let r;
    try {
      r = await fetch(url, { signal });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new Error("Cannot reach the viewer server. Is `make ui` still running?");
    }
    let body = null;
    try {
      body = await r.json();
    } catch (_) {
      /* non-JSON error page */
    }
    if (!r.ok) throw new Error((body && body.error) || `Request failed (${r.status})`);
    return body;
  }

  // ---------- tiny Markdown renderer (React elements only: script text is always escaped) ----------
  function inline(text, keyBase) {
    return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
      const k = `${keyBase}-${i}`;
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4)
        return html`<strong key=${k}>${part.slice(2, -2)}</strong>`;
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2)
        return html`<code key=${k}>${part.slice(1, -1)}</code>`;
      return part;
    });
  }
  const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  function Markdown({ src }) {
    const lines = src.split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const k = `b${i}`;
      if (line.startsWith("```")) {
        const buf = [];
        for (i++; i < lines.length && !lines[i].startsWith("```"); i++) buf.push(lines[i]);
        i++;
        out.push(html`<pre key=${k}>${buf.join("\n")}</pre>`);
        continue;
      }
      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      if (h) {
        const Tag = `h${Math.min(h[1].length + 1, 5)}`;
        out.push(html`<${Tag} key=${k}>${inline(h[2], k)}<//>`);
        i++;
        continue;
      }
      if (line.trim().startsWith("|")) {
        const rows = [];
        for (; i < lines.length && lines[i].trim().startsWith("|"); i++) rows.push(lines[i]);
        const sep = rows.length > 1 && /^[\s|:-]+$/.test(rows[1]);
        const head = sep ? cells(rows[0]) : null;
        const body = (sep ? rows.slice(2) : rows).map(cells);
        out.push(html`<div class="table-wrap" key=${k}><table>
          ${head && html`<thead><tr>${head.map((c, j) => html`<th key=${j}>${inline(c, `${k}h${j}`)}</th>`)}</tr></thead>`}
          <tbody>${body.map((r, ri) => html`<tr key=${ri}>${r.map((c, j) => html`<td key=${j}>${inline(c, `${k}r${ri}c${j}`)}</td>`)}</tr>`)}</tbody>
        </table></div>`);
        continue;
      }
      if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
        const items = [];
        for (; i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i]); i++)
          items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        out.push(html`<ul key=${k}>${items.map((t, j) => html`<li key=${j}>${inline(t, `${k}l${j}`)}</li>`)}</ul>`);
        continue;
      }
      if (!line.trim()) {
        i++;
        continue;
      }
      const buf = [];
      for (; i < lines.length && lines[i].trim() && !/^(#|\||```|\s*([-*]|\d+\.)\s)/.test(lines[i]); i++)
        buf.push(lines[i]);
      out.push(html`<p key=${k}>${inline(buf.join(" "), k)}</p>`);
    }
    return html`<div class="md">${out}</div>`;
  }

  // ---------- components ----------
  function Bars({ scores }) {
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

  function Beat({ label, beat, segments, hookRule }) {
    if (!beat) return html`<section><h3>${label}</h3><p class="muted">Not identified.</p></section>`;
    const off = offsetInPromo(beat, segments);
    const late = hookRule && off != null && off > 3;
    return html`<section>
      <h3>${label}</h3>
      <div class="beat-meta">
        Ep ${beat.episode} · ${beat.ts}
        ${off != null ? html` · <span class=${late ? "warn-text" : ""}>${off.toFixed(1)}s into the promo${late ? " (hook should land in the first 3s)" : ""}</span>` : null}
      </div>
      <blockquote>${beat.speaker ? html`<b>${beat.speaker}: </b>` : null}${beat.text}</blockquote>
    </section>`;
  }

  function CopyButton({ text, label }) {
    const [state, setState] = useState("");
    useEffect(() => {
      if (!state) return;
      const t = setTimeout(() => setState(""), 1800);
      return () => clearTimeout(t);
    }, [state]);
    const copy = async () => {
      try {
        await navigator.clipboard.writeText(text);
        setState("Copied");
      } catch (_) {
        setState("Copy failed");
      }
    };
    return html`<button type="button" class="btn small" onClick=${copy} aria-live="polite">${state || label}</button>`;
  }

  function Detail({ p, detailRef }) {
    const segText = p.segments.map((s) => `Ep ${s.episode}  ${s.start_ts} - ${s.end_ts}`).join("\n");
    const tags = [
      ...(p.risks || []).map((r) => ({ t: r.startsWith("needs context:") ? r : flagLabel(r), warn: true })),
      ...(p.flags || []).map((r) => ({ t: flagLabel(r), warn: true })),
      ...(p.end_refined ? [{ t: `end tightened (was ${p.original_end_ts}, ${dur(p.original_duration_s)})` }] : []),
      ...(p.ends_at_episode_end ? [{ t: "ends at the episode's end" }] : []),
    ];
    return html`<article class="panel detail" ref=${detailRef} tabIndex="-1" aria-labelledby="detail-title">
      <header class="detail-head">
        <div>
          <h2 id="detail-title">#${p.rank} <span class="muted">· ${p.kind === "single" ? "single clip" : p.kind} · ${dur(p.total_duration_s)}</span></h2>
          <div class="muted">confidence ${fmt(p.confidence * 10)}/10</div>
        </div>
        <div class="score-big" title="Final score (0–10)">${fmt(p.final_score)}</div>
      </header>

      <section>
        <div class="row-between"><h3>Cut list</h3><${CopyButton} text=${segText} label="Copy timestamps" /></div>
        <table class="cuts">
          <thead><tr><th>#</th><th>Episode</th><th>Start</th><th>End</th><th>Length</th></tr></thead>
          <tbody>${p.segments.map((s, i) => html`<tr key=${i}>
            <td>${i + 1}</td><td>${s.episode}</td><td class="mono">${s.start_ts}</td><td class="mono">${s.end_ts}</td><td>${dur(s.end_s - s.start_s)}</td>
          </tr>`)}</tbody>
        </table>
      </section>

      <section><h3>Why it works</h3><p>${p.why || html`<span class="muted">No reason given.</span>`}</p></section>
      ${p.post_first_because ? html`<section><h3>Post it early because</h3><p>${p.post_first_because}</p></section>` : null}
      <${Beat} label="Hook" beat=${p.hook} segments=${p.segments} hookRule=${true} />
      <${Beat} label="Cliffhanger" beat=${p.cliffhanger} segments=${p.segments} />
      <section><h3>Scores</h3><${Bars} scores=${p.scores || {}} /></section>
      <section>
        <h3>Things to check before posting</h3>
        ${tags.length
          ? html`<ul class="tags">${tags.map((x, i) => html`<li key=${i} class=${"tag" + (x.warn ? " warn" : "")}>${x.t}</li>`)}</ul>`
          : html`<p class="muted">Nothing flagged.</p>`}
      </section>
    </article>`;
  }

  function PromoList({ promos, sel, onSelect }) {
    const listRef = useRef(null);
    useEffect(() => {
      const el = listRef.current && listRef.current.querySelector('[aria-current="true"]');
      if (el) el.scrollIntoView({ block: "nearest" });
    }, [sel]);
    return html`<nav class="list" ref=${listRef} aria-label="Ranked promos">
      ${promos.map((p, i) => {
        const warn = (p.flags || []).length > 0 || (p.risks || []).includes("weak_ending");
        return html`<button type="button" key=${p.id} class="item" aria-current=${i === sel ? "true" : "false"} onClick=${() => onSelect(i, true)}>
          <span class="top"><span>#${p.rank} · Ep ${p.segments.map((s) => s.episode).filter((e, j, a) => a.indexOf(e) === j).join(", ")}</span><span class="score">${fmt(p.final_score)}</span></span>
          <span class="sub">${p.segments[0].start_ts} · ${dur(p.total_duration_s)}${p.segments.length > 1 ? ` · ${p.segments.length} parts` : ""}${warn ? html` · <span class="warn-text">check</span>` : null}</span>
        </button>`;
      })}
    </nav>`;
  }

  function State({ kind, title, children }) {
    return html`<div class=${"state" + (kind ? " " + kind : "")} role=${kind === "err" ? "alert" : "status"}>
      ${title ? html`<b>${title}</b>` : null}${children}
    </div>`;
  }

  function App() {
    const init = useRef(readHash()).current;
    const [runs, setRuns] = useState(null);
    const [runsErr, setRunsErr] = useState("");
    const [runId, setRunId] = useState(init.run);
    const [run, setRun] = useState(null);
    const [runErr, setRunErr] = useState("");
    const [loading, setLoading] = useState(false);
    const [variant, setVariant] = useState(init.v);
    const [sel, setSel] = useState(0);
    const [tab, setTab] = useState(init.tab === "report" ? "report" : "promos");
    const [reload, setReload] = useState(0);
    const wantPromo = useRef(init.p);
    const detailRef = useRef(null);

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
      setRunErr("");
      getJSON(`/api/run?id=${encodeURIComponent(runId)}`, ac.signal)
        .then((r) => {
          setRun(r);
          setVariant((v) => (r.variants[v] ? v : "main"));
          const promos = (r.variants.main || {}).promos || [];
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

    const data = run && (run.variants[variant] || run.variants.main);
    const promos = (data && data.promos) || [];
    const cur = runs && runs.find((r) => r.id === runId);
    const selected = promos[Math.min(sel, promos.length - 1)];

    useEffect(() => {
      writeHash({ run: runId, v: variant, p: selected && selected.id, tab });
    }, [runId, variant, selected, tab]);

    const select = useCallback((i, fromClick) => {
      setSel(i);
      if (fromClick && window.matchMedia("(max-width: 760px)").matches && detailRef.current) {
        requestAnimationFrame(() => detailRef.current.scrollIntoView({ behavior: "smooth", block: "start" }));
      }
    }, []);

    // j/k or arrow keys move through the ranking (ignored while a form control has focus)
    useEffect(() => {
      const onKey = (e) => {
        if (tab !== "promos" || !promos.length || e.metaKey || e.ctrlKey || e.altKey) return;
        if (e.target.closest && e.target.closest("select, input, textarea")) return;
        const down = e.key === "ArrowDown" || e.key === "j";
        const up = e.key === "ArrowUp" || e.key === "k";
        if (!down && !up) return;
        e.preventDefault();
        setSel((s) => Math.max(0, Math.min(promos.length - 1, s + (down ? 1 : -1))));
      };
      document.addEventListener("keydown", onKey);
      return () => document.removeEventListener("keydown", onKey);
    }, [tab, promos.length]);

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
        <h1>Promo Finder</h1>
        <label class="field">
          <span>Run</span>
          <select value=${runId} onChange=${(e) => { setRunId(e.target.value); setVariant("main"); }}>
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
              <select value=${variant} onChange=${(e) => { setVariant(e.target.value); setSel(0); }}>
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
      </header>

      ${cur && cur.partial
        ? html`<div class="banner" role="note">Partial run: LLM scoring stopped early (no key, quota or budget), so some promos are ranked on rule scores only.</div>`
        : null}
      ${variant !== "main" && run && run.variants[variant]
        ? html`<div class="banner info" role="note">You are viewing an ablation (a robustness check), not the ranking to post from.</div>`
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
                  ? html`<${PromoList} promos=${promos} sel=${sel} onSelect=${select} />`
                  : null}
                ${selected
                  ? html`<${Detail} p=${selected} detailRef=${detailRef} />`
                  : html`<${State} title="No promos in this ranking.">The tool found no candidate that passed its hook and cliffhanger gates.<//>`}
              </main>`}
      <footer class="foot muted">Local only · outputs quote the confidential script: don't share screenshots publicly · ↑/↓ or j/k to move through the ranking</footer>
    </div>`;
  }

  ReactDOM.createRoot(root).render(html`<${App} />`);
})();
