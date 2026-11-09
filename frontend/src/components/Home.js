import { html } from "../ui.js";
import { AnalyzeForm } from "./Analyze.js";
import { KIND, demoScoring } from "../format.js";

/** Screen 1: what the tool does, upload your own screenplay, and the latest results. */
export function Home({ caps, cur, isDemo, links, onSubmitted }) {
  return html`<main class="home">
    <section class="workspace-heading home-hero">
      <div>
        <div class="eyebrow"><span class="hero-line" aria-hidden="true"></span> YOUR NEXT GREAT PROMO STARTS HERE</div>
        <h2>Find the promos hiding in your screenplay</h2>
        <p>Upload the episodes of a microdrama. Get a ranked shortlist of 20–60 second promos with exact cuts, the hook, the cliffhanger and why each one works.</p>
      </div>
      <div class="hero-aside"><div class="hero-format" aria-hidden="true"><span>20–60</span><small>SECONDS. ALL IMPACT.</small></div></div>
    </section>

    ${caps
      ? html`<${AnalyzeForm} caps=${caps} onSubmitted=${onSubmitted} />`
      : html`<section class="panel analyze"><h2>Run it on a show folder</h2>
          <p class="muted">Uploads are not available on this server. Run the tool from the command line and reload:</p>
          <pre>make run-rules SHOW_DIR="path/to/show"   # free, no API key\nmake run SHOW_DIR="path/to/show"         # with the LLM in .env</pre></section>`}

    ${cur
      ? html`<section class="panel latest" aria-labelledby="latest-title">
          <h2 id="latest-title">${isDemo ? "Explore the sample results" : "Latest results"}</h2>
          <p class="muted"><b>${cur.show}</b> · ${KIND[cur.kind] || "Analysis"} · ${cur.count} promos · ${cur.model || "unknown model"}</p>
          ${isDemo ? html`<p class="muted small">Fictional sample story. ${demoScoring(cur)}. Reviews on the sample are shared and reset when the service restarts.</p>` : null}
          <div class="latest-actions">
            <a class="btn primary" href=${links.shortlist}>Open the shortlist</a>
            <a class="btn" href=${links.report}>Read the quality report</a>
          </div>
        </section>`
      : null}
  </main>`;
}
