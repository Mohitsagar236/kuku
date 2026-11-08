import { html } from "./ui.js";
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

export function Markdown({ src }) {
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

