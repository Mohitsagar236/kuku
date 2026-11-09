import { html } from "../ui.js";

/** Top navigation: one link per screen. The URL hash is the router. */
export function MainNav({ screen, links, hasRun }) {
  const item = (id, label, disabled) => html`<a key=${id} href=${disabled ? undefined : links[id]}
    class=${"mainnav-link" + (screen === id ? " on" : "") + (disabled ? " off" : "")}
    aria-current=${screen === id ? "page" : undefined} aria-disabled=${disabled ? "true" : undefined}>${label}</a>`;
  return html`<nav class="mainnav" aria-label="Main">
    ${item("home", "Home", false)}
    ${screen === "analyzing" ? html`<span class="mainnav-link on busy" aria-current="page"><i class="mainnav-dot" aria-hidden="true"></i>Analyzing</span>` : null}
    ${item("shortlist", "Shortlist", !hasRun)}
    ${item("report", "Report", !hasRun)}
  </nav>`;
}
