import { html } from "../ui.js";
import { useState, useEffect, useRef } from "react";
import { getJSON } from "../api.js";
import { AnalysisProgress } from "./AnalysisProgress.js";

/** Polls one analysis job by token (kept in the URL, so a reload resumes it). */
export function AnalyzingScreen({ job, onDone, onBack }) {
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");
  const timer = useRef(null);
  const doneRef = useRef(onDone); // the parent re-creates onDone every render; poll with the latest
  doneRef.current = onDone;

  useEffect(() => {
    let alive = true;
    setError("");
    async function poll() {
      let r;
      try { r = await getJSON(`/api/analyze/status?job=${encodeURIComponent(job)}`); }
      catch (e) { if (alive) setError(/not found|expired/i.test(e.message) ? "This analysis has expired or was not found. Start a new one." : e.message); return; }
      if (!alive) return;
      if (r.progress) setProgress(r.progress);
      if (r.state === "done") { doneRef.current(r.run); return; }
      if (r.state === "error") { setError(r.error || "The analysis failed."); return; }
      timer.current = setTimeout(poll, 1000);
    }
    poll();
    return () => { alive = false; clearTimeout(timer.current); };
  }, [job]);

  return html`<section class="panel analyzing" aria-labelledby="analyzing-title">
    <h2 id="analyzing-title">Analyzing your screenplay</h2>
    ${error
      ? html`<div class="banner err" role="alert">${error}</div>
             <button type="button" class="btn" onClick=${onBack}>Back to upload</button>`
      : html`<p class="muted">You can leave this page open. The shortlist opens by itself when the analysis is done.</p>
             <${AnalysisProgress} progress=${progress} />`}
  </section>`;
}
