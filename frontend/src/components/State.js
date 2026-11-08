import { html } from "../ui.js";

export function State({ kind, title, children }) {
  return html`<div class=${"state" + (kind ? " " + kind : "")} role=${kind === "err" ? "alert" : "status"}>
    ${title ? html`<b>${title}</b>` : null}${children}
  </div>`;
}

