import { html } from "../ui.js";
import { useState } from "react";
import { sendJSON } from "../api.js";

export function AnalyzeForm({ caps, onSubmitted }) {
  const [files, setFiles] = useState([]);
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState("rules");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const model = (caps && caps.model) || { available: false, name: "", reason: "" };
  const pick = (e) => {
    setFiles(Array.from(e.target.files || []).filter((f) => f.name.toLowerCase().endsWith(".txt")));
    setError("");
  };
  const total = files.reduce((n, f) => n + f.size, 0);
  const canRun = !busy && files.length > 0 && (mode === "rules" || model.available);

  async function run(ev) {
    ev.preventDefault();
    if (!canRun) return;
    setBusy(true); setError("");
    const payload = await Promise.all(files.map(async (f) => ({ name: f.name, text: await f.text() })));
    const r = await sendJSON("/api/analyze", { files: payload, mode, title });
    setBusy(false);
    if (!r.ok) {
      setError(r.status === 404 || r.status === 405
        ? "This server does not support uploads (it may be running an older version). Restart it and reload the page."
        : (r.data && r.data.error) || `Upload failed (${r.status})`);
      return;
    }
    onSubmitted(r.data.job);
  }

  const lim = caps && caps.limits;
  return html`<section class="panel analyze" aria-labelledby="analyze-title">
    <div class="analyze-head">
      <h2 id="analyze-title">Analyze your own screenplay</h2>
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
        <label><input type="radio" name="mode" value="rules" checked=${mode === "rules"} onChange=${() => setMode("rules")} /> <b>Rules only</b> <span class="muted">· free, seconds</span></label>
        <label><input type="radio" name="mode" value="model" checked=${mode === "model"} disabled=${!model.available} onChange=${() => setMode("model")} /> <b>With an AI model</b> <span class="muted">· ${model.available ? `${model.name}, takes a few minutes` : "unavailable"}</span></label>
        ${!model.available && model.reason ? html`<p class="muted small" role="note">${model.reason}</p>` : null}
      </fieldset>
      <p class="muted small">${caps && caps.public
        ? `Files are processed on this server in a private, unlisted run and deleted after ${caps.retention_minutes} minutes. Do not upload anything you are not allowed to send to this server.`
        : `Files are processed on this machine in a private run and deleted after ${caps ? caps.retention_minutes : 60} minutes.`}</p>
      ${error ? html`<div class="banner err" role="alert">${error}</div>` : null}
      <button type="submit" class="btn primary" disabled=${!canRun}>${busy ? "Uploading…" : "Find promos"}</button>
    </form>
  </section>`;
}
