"""Read-only local server for the promo viewer.

Serves the static React app in this folder and a tiny JSON API over outputs/.
Binds to 127.0.0.1 only and rejects foreign Host headers (DNS rebinding):
the outputs quote the (confidential) script.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
OUTPUTS = (HERE.parent / "outputs").resolve()
STATIC = {"index.html", "app.js", "style.css"}
# Files an editor may download from a run folder; nothing else (llm_raw/, metadata) is served.
DOWNLOADS = {"promos.csv": "text/csv", "ranking.md": "text/markdown"}
CSP = (
    "default-src 'self'; script-src 'self' https://unpkg.com; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
log = logging.getLogger("promo-viewer")


def _kind(run_dir: Path) -> str:
    if run_dir.name == "rules_baseline":
        return "baseline"
    return "smoke" if run_dir.name.startswith("smoke-") else "full"


def list_runs(outputs: Path | None = None) -> list[dict]:
    outputs = outputs or OUTPUTS
    runs = []
    for f in outputs.glob("*/*/promos.json"):
        d = f.parent
        try:
            meta = json.loads(f.read_text())
            promos = meta.get("promos", [])
        except (OSError, ValueError, AttributeError):
            log.warning("skipping unreadable run %s", d.relative_to(outputs).as_posix())
            continue
        runs.append(
            {
                "id": d.relative_to(outputs).as_posix(),
                "show": meta.get("show") or d.parent.name,
                "kind": _kind(d),
                "generated_at": meta.get("generated_at"),
                "model": meta.get("model"),
                "partial": bool(meta.get("partial")),
                "count": len(promos),
            }
        )
    runs.sort(key=lambda r: r["generated_at"] or "", reverse=True)
    return runs


def run_dir(run_id: str, outputs: Path | None = None) -> Path | None:
    """Resolve a run id to its folder, or None if it is not a run inside outputs/."""
    outputs = outputs or OUTPUTS
    if not run_id or "\x00" in run_id:
        return None
    d = (outputs / run_id).resolve()
    if outputs not in d.parents or not (d / "promos.json").is_file():
        return None
    return d


def load_run(run_id: str, outputs: Path | None = None) -> dict | None:
    d = run_dir(run_id, outputs)
    if d is None:
        return None
    variants = {}
    for f in sorted(d.glob("promos*.json")):
        name = "main" if f.name == "promos.json" else f.stem.removeprefix("promos_")
        variants[name] = json.loads(f.read_text())
    report = d / "quality_report.md"
    return {
        "id": run_id,
        "variants": variants,
        "report": report.read_text() if report.is_file() else None,
        "downloads": sorted(n for n in DOWNLOADS if (d / n).is_file()),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PromoViewer"
    sys_version = ""

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: object, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            return self._send(403, b"forbidden host", "text/plain")
        try:
            self._route()
        except Exception:  # never drop the connection: the UI shows a retryable error
            log.exception("request failed: %s", self.path)
            self._json({"error": "server error while reading outputs (see terminal)"}, 500)

    def _route(self) -> None:
        url = urlparse(self.path)
        q = parse_qs(url.query)
        run_id = q.get("id", [""])[0]
        if url.path == "/api/runs":
            return self._json(list_runs())
        if url.path == "/api/run":
            try:
                run = load_run(run_id)
            except ValueError:
                return self._json({"error": f"run {run_id!r} has a corrupt promos file"}, 500)
            return self._json(run) if run else self._json({"error": "run not found"}, 404)
        if url.path == "/api/file":
            name = q.get("name", [""])[0]
            d = run_dir(run_id)
            if d is None or name not in DOWNLOADS or not (d / name).is_file():
                return self._json({"error": "file not found"}, 404)
            fname = f"{d.name}-{name}".replace('"', "")
            return self._send(
                200,
                (d / name).read_bytes(),
                f"{DOWNLOADS[name]}; charset=utf-8",
                {"Content-Disposition": f'attachment; filename="{fname}"'},
            )
        rel = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
        if rel not in STATIC:
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, (HERE / rel).read_bytes(), ctype)

    def log_message(self, *_args: object) -> None:  # keep the terminal quiet
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local viewer for promo-finder outputs/")
    ap.add_argument("--port", type=int, default=8765)
    port = ap.parse_args(argv).port
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"error: cannot listen on 127.0.0.1:{port} ({e.strerror}); "
              f"try: make ui PORT={port + 1}", file=sys.stderr)  # fmt: skip
        return 2
    if not OUTPUTS.is_dir():
        print(f"note: {OUTPUTS} does not exist yet; run `make run-rules SHOW_DIR=...` first")
    print(f"Promo viewer: http://127.0.0.1:{port}  (local only; outputs are confidential)")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()
    srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
