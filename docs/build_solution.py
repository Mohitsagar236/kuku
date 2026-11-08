"""Render docs/SOLUTION.html into two PDFs and check each is at most 2 pages.

* docs/SOLUTION.pdf             public (committed): no script quotes, moments are described.
* docs/SOLUTION_submission.pdf  private (git-ignored, for the form): adds short hook and
                                cliffhanger quotes, pulled from outputs/ at build time.

The template's <!--top3 ... --> block lists promo ids and hand-written, name-free labels; the
build fails if those ids are not the top 3 of the run it names, so the doc cannot drift from
the outputs. Usage: .venv/bin/python docs/build_solution.py   (or: make docs)
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
MAX_PAGES = 2
QUOTE_WORDS = 9  # "short quotes" only, even in the private copy
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
BLOCK = re.compile(r"<!--top3 run=(\S+)\n(.*?)\n-->", re.S)


def chrome() -> str:
    for c in CHROME_CANDIDATES:
        found = shutil.which(c) or (c if Path(c).is_file() else None)
        if found:
            return found
    sys.exit("error: Chrome/Chromium not found (needed to print the PDF)")


def short(text: str) -> str:
    words = text.split()
    return " ".join(words[:QUOTE_WORDS]) + ("…" if len(words) > QUOTE_WORDS else "")


def table(run_rel: str, rows: list[tuple[str, str]], quotes: bool) -> str:
    promos = json.loads((ROOT / "outputs" / run_rel / "promos.json").read_text())["promos"]
    top = promos[: len(rows)]
    if [p["id"] for p in top] != [pid for pid, _ in rows]:
        sys.exit(f"error: the doc's top-3 ids do not match {run_rel}; update the <!--top3--> block")
    moment = "Opens on → cuts on" if quotes else "Moment"
    head = f"<tr><th>#</th><th>Cut</th><th>{moment}</th><th>Hook / cliff / alone</th></tr>"
    out = [f'<table class="top3">{head}']
    for p, (_, label) in zip(top, rows, strict=True):
        cut = " + ".join(f"Ep {s['episode']} {s['start_ts']}–{s['end_ts']}" for s in p["segments"])
        sc = p["scores"]
        h, c = p["hook"], p["cliffhanger"]
        quoted = f"“{html.escape(short(h['text']))}” → “{html.escape(short(c['text']))}”"
        cells = [
            str(p["rank"]),
            f"{cut} ({round(p['total_duration_s'])} s)",
            quoted if quotes else html.escape(label),  # private copy quotes; public describes
            f"{sc['hook']:.1f} / {sc['cliffhanger']:.1f} / {sc['clarity']:.1f}",
        ]
        out.append("<tr>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>")
    return "\n".join(out) + "</table>"


def render(src: str, quotes: bool, dest: Path) -> int:
    m = BLOCK.search(src)
    if not m:
        sys.exit("error: <!--top3 run=...--> block missing from SOLUTION.html")
    rows = [tuple(x.split("|", 1)) for x in m.group(2).strip().splitlines()]
    page = src[: m.start()] + table(m.group(1), rows, quotes) + src[m.end() :]  # type: ignore[arg-type]
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "solution.html"
        f.write_text(page)
        subprocess.run(
            [chrome(), "--headless=new", "--disable-gpu", f"--user-data-dir={tmp}/prof",
             "--no-pdf-header-footer", f"--print-to-pdf={dest}", f.as_uri()],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
    return len(PdfReader(str(dest)).pages)


def main() -> int:
    src = (DOCS / "SOLUTION.html").read_text()
    ok = True
    for name, quotes in (("SOLUTION.pdf", False), ("SOLUTION_submission.pdf", True)):
        n = render(src, quotes, DOCS / name)
        ok &= n <= MAX_PAGES
        print(f"{'OK  ' if n <= MAX_PAGES else 'FAIL'} docs/{name}: {n} page(s)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
