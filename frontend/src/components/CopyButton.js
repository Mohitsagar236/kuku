import { html } from "../ui.js";
import { useState, useEffect } from "react";
export function CopyButton({ text, label }) {
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

