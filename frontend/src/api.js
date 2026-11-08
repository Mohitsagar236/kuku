export async function getJSON(url, signal) {
  let r;
  try {
    r = await fetch(url, { signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error("Cannot reach the viewer server. Is `make ui` still running?");
  }
  let body = null;
  try {
    body = await r.json();
  } catch (_) {
    /* non-JSON error page */
  }
  if (!r.ok) throw new Error((body && body.error) || `Request failed (${r.status})`);
  return body;
}

// POST JSON. Never throws: returns {ok, status, data} so callers can show 422 nudge help.
export async function sendJSON(url, body) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await r.json();
    } catch (_) {
      /* non-JSON */
    }
    return { ok: r.ok, status: r.status, data };
  } catch (_) {
    return { ok: false, status: 0, data: { error: "Cannot reach the viewer server. Is `make ui` still running?" } };
  }
}

