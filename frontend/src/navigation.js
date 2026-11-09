export const SCREENS = ["home", "analyzing", "shortlist", "report"];

/** The URL hash is the single source of truth: #s=<screen>&run=&v=&p=&job=
 *  Old links keep working: #run=... opens the shortlist, #tab=report opens the report. */
export function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  const run = h.get("run") || "";
  const p = h.get("p") || "";
  const job = h.get("job") || "";
  let s = h.get("s");
  if (!SCREENS.includes(s)) s = h.get("tab") === "report" ? "report" : job ? "analyzing" : run || p ? "shortlist" : "home";
  return { run, v: h.get("v") || "main", p, job, s, tab: s === "report" ? "report" : "promos" };
}

export function hashFor(st) {
  const h = new URLSearchParams();
  if (st.s && st.s !== "home") h.set("s", st.s);
  if (st.s === "analyzing") {
    if (st.job) h.set("job", st.job);
  } else if (st.s !== "home") {
    if (st.run) h.set("run", st.run);
    if (st.v && st.v !== "main") h.set("v", st.v);
    if (st.s === "shortlist" && st.p) h.set("p", st.p);
  }
  return "#" + h.toString();
}

export function writeHash(st) {
  const next = hashFor(st);
  const same = location.hash === next || (next === "#" && location.hash === "");
  if (!same) history.replaceState(null, "", next);
}
