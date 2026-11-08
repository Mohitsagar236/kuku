export function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  return { run: h.get("run") || "", v: h.get("v") || "main", p: h.get("p") || "", tab: h.get("tab") || "promos" };
}
export function writeHash(s) {
  const h = new URLSearchParams();
  if (s.run) h.set("run", s.run);
  if (s.v && s.v !== "main") h.set("v", s.v);
  if (s.p) h.set("p", s.p);
  if (s.tab && s.tab !== "promos") h.set("tab", s.tab);
  const next = "#" + h.toString();
  if (location.hash !== next) history.replaceState(null, "", next);
}

