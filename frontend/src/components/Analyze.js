import { html } from "../ui.js";
import { useState, useEffect, useRef } from "react";
import { getJSON, sendJSON } from "../api.js";

const PROVIDERS = [["gemini", "Google Gemini"], ["openai", "OpenAI"], ["anthropic", "Anthropic"], ["groq", "Groq"]];

export function Analyze({ onDone, onClose }) {
  const [caps, setCaps] = useState(null);
  const [files, setFiles] = useState([]);
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState("rules");
  const [provider, setProvider] = useState("gemini");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const timer = useRef(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    getJSON("/api/capabilities").then(setCaps).catch((e) => setError(e.message));
    return () => { alive.current = false; clearTimeout(timer.current); };
  }, []);

  const pick = (e) => {
    setFiles(Array.from(e.target.files || []).filter((f) => f.name.toLowerCase().endsWith(".txt")));
    setError("");
  };
  const total = files.reduce((n, f) => n + f.size, 0);
  const needsKey = mode === "model" && caps && caps.needs_key_for_model;
  const canRun = !busy && files.length > 0 && (!needsKey || key.trim().length > 0);

  async function poll(job) {
    const r = await sendStatus(job);
    if (!alive.current) return;
    if (r.state === "done") { setBusy(false); setKey(""); onDone(r.run); return; }
    if (r.state === "error") { setBusy(false); setError(r.error || "The analysis failed."); return; }
    setStage(r.stage || "Working");
    timer.current = setTimeout(() => poll(job), 1500);
  }
  async function sendStatus(job) {
    try { return await getJSON(`/api/analyze/status?job=${encodeURIComponent(job)}`); }
    catch (e) { return { state: "error", error: e.message }; }
  }

  async function run(ev) {
    ev.preventDefault();
    if (!canRun) return;
    setBusy(true); setError(""); setStage("Uploading");
    const payload = await Promise.all(files.map(async (f) => ({ name: f.name, text: await f.text() })));
    const body = { files: payload, mode, title };
    if (mode === "model" && key.trim()) { body.api_key = key.trim(); body.provider = provider; }
    const r = await sendJSON("/api/analyze", body);
    if (!r.ok) { setBusy(false); setError((r.data && r.data.error) || `Upload failed (${r.status})`); return; }
    setStage("Queued");
    poll(r.data.job);
  }

  const lim = caps && caps.limits;
  return html`<section class="panel analyze" aria-labelledby="analyze-title">
    <div class="analyze-head">
      <h2 id="analyze-title">Analyze your own screenplay</h2>
      ${onClose ? html`<button type="button" class="btn small" onClick=${onClose} disabled=${busy}>Close</button>` : null}
    </div>
    <p class="muted">Choose the episode <code>.txt</code> files of one show (the same timestamped screenplay format as the input pack). You get a ranked shortlist with exact cuts, hooks, cliffhangers and a quality report.</p>
    <form onSubmit=${run} class="analyze-form">
      <label class="field"><span>Episode files</span>
        <input type="file" multiple accept=".txt,text/plain" onChange=${pick} aria-label="Episode files" disabled=${busy} />
      </label>
      <div class="muted small" aria-live="polite">${files.length ? `${files.length} file${files.length > 1 ? "s" : ""}, ${(total / 1000).toFixed(0)} KB` : lim ? `Up to ${lim.files} files, ${lim.total_mb} MB in total.` : ""}</div>
      <label class="field"><span>Name (optional)</span>
        <input type="text" maxLength="80" value=${title} onInput=${(e) => setTitle(e.target.value)} placeholder="Your screenplay" aria-label="Name" disabled=${busy} />
      </label>
      <fieldset class="modes" disabled=${busy}>
        <legend>How to rank</legend>
        <label><input type="radio" name="mode" value="rules" checked=${mode === "rules"} onChange=${() => setMode("rules")} /> <b>Rules only</b> <span class="muted">· free, no key, seconds</span></label>
        <label><input type="radio" name="mode" value="model" checked=${mode === "model"} onChange=${() => setMode("model")} /> <b>With an AI model</b> <span class="muted">· better judgement, minutes${needsKey ? ", needs your API key" : ""}</span></label>
      </fieldset>
      ${mode === "model"
        ? html`<div class="keybox">
            <label class="field"><span>Provider</span>
              <select value=${provider} onChange=${(e) => setProvider(e.target.value)} aria-label="Provider" disabled=${busy}>
                ${PROVIDERS.map(([v, l]) => html`<option key=${v} value=${v}>${l}</option>`)}
              </select></label>
            <label class="field"><span>API key${needsKey ? "" : " (optional here)"}</span>
              <input type="password" autocomplete="off" spellcheck="false" value=${key} onInput=${(e) => setKey(e.target.value)} aria-label="API key" disabled=${busy} placeholder=${needsKey ? "Paste your key" : "Uses the key in .env if empty"} />
            </label>
            <p class="muted small">Your key is used for this one analysis only. It is never stored, logged or shown again. A free-tier key limits speed (about 70 calls for 50 episodes, several minutes).</p>
          </div>`
        : null}
      <p class="muted small">${caps && caps.public
        ? `Files are processed on this server in a private, unlisted run and deleted after ${caps.retention_minutes} minutes. Do not upload anything you are not allowed to send to this server.`
        : `Files are processed on this machine in a private run and deleted after ${caps ? caps.retention_minutes : 60} minutes.`}</p>
      ${error ? html`<div class="banner err" role="alert">${error}</div>` : null}
      ${busy ? html`<div class="state" role="status" aria-live="polite"><b>Analysing…</b> ${stage}</div>` : null}
      <button type="submit" class="btn primary" disabled=${!canRun}>${busy ? "Analysing…" : "Find promos"}</button>
    </form>
  </section>`;
}
