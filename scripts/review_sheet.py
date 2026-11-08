"""Human review sheet for the top promos, and a comparison against the model ranking.

    make    write a CSV with blank 1-5 ratings and an Accept/Revise/Reject column
    compare read the filled CSV and report agreement with the model ranking

Nothing is ever pre-filled. `compare` refuses sheets with no genuine ratings, so a
blank sheet cannot be mistaken for human validation. The sheet quotes short script
lines, so it belongs under outputs/ (git-ignored) and must not be committed.

Usage:
    .venv/bin/python scripts/review_sheet.py make    RUN_DIR [--top 20] [--out sheet.csv]
    .venv/bin/python scripts/review_sheet.py compare RUN_DIR SHEET.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

RATINGS = ["hook", "cliffhanger", "standalone", "emotion", "usefulness"]
VERDICTS = {"accept", "revise", "reject"}
FIELDS = [
    "rank", "promo_id", "episode", "start", "end", "model_hook", "model_cliffhanger",
    "hook_line", "cliffhanger_line",
    *[f"{r}_1to5" for r in RATINGS], "verdict_accept_revise_reject", "notes",
]  # fmt: skip


def _promos(run: Path) -> list[dict]:
    return json.loads((run / "promos.json").read_text())["promos"]


def make(run: Path, top: int, out: Path) -> int:
    rows = []
    for p in _promos(run)[:top]:
        segs = p["segments"]
        rows.append({
            "rank": p["rank"], "promo_id": p["id"],
            "episode": "+".join(str(s["episode"]) for s in segs),
            "start": " | ".join(s["start_ts"] for s in segs),
            "end": " | ".join(s["end_ts"] for s in segs),
            "model_hook": p["scores"]["hook"], "model_cliffhanger": p["scores"]["cliffhanger"],
            "hook_line": " ".join(p["hook"]["text"].split()[:12]),
            "cliffhanger_line": " ".join(p["cliffhanger"]["text"].split()[:12]),
        })  # fmt: skip
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} promos, all ratings blank)")
    return 0


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra)
    vb = sum((y - mb) ** 2 for y in rb)
    return None if va == 0 or vb == 0 else cov / (va * vb) ** 0.5


def load_filled(sheet: Path) -> list[dict]:
    """Rows with a complete, valid review. Partial or invalid rows are skipped, not guessed."""
    out = []
    for row in csv.DictReader(sheet.open()):
        try:
            vals = {r: int(row[f"{r}_1to5"]) for r in RATINGS}
        except (ValueError, TypeError, KeyError):
            continue
        verdict = (row.get("verdict_accept_revise_reject") or "").strip().lower()
        if verdict in VERDICTS and all(1 <= v <= 5 for v in vals.values()):
            out.append({**row, **vals, "verdict": verdict, "rank": int(row["rank"])})
    return out


def compare(run: Path, sheet: Path) -> int:
    rows = load_filled(sheet)
    if not rows:
        print("NO GENUINE REVIEWS: no row has all five 1-5 ratings and a verdict. "
              "Human validation is still pending.")  # fmt: skip
        return 1
    n = len(rows)
    overall = [sum(r[k] for k in RATINGS) / len(RATINGS) for r in rows]
    model_pos = [-float(r["rank"]) for r in rows]  # higher = model liked it more
    rho = spearman(model_pos, overall)
    v = {k: sum(r["verdict"] == k for r in rows) for k in sorted(VERDICTS)}
    print(f"reviewed {n} promos; verdicts {v}")
    print("mean rating per criterion: " + ", ".join(
        f"{k} {sum(r[k] for r in rows) / n:.2f}" for k in RATINGS))  # fmt: skip
    print("Spearman, model rank vs human mean rating: "
          + ("n/a (need 3+ varied reviews)" if rho is None else f"{rho:.2f}"))  # fmt: skip
    top5 = [r for r in rows if r["rank"] <= 5]
    if top5:
        print(
            "model top-5 reviewed: "
            f"{sum(r['verdict'] == 'accept' for r in top5)}/{len(top5)} accepted"
        )
    print("Biggest disagreements (model rank vs human mean):")
    mean = {r["promo_id"]: sum(r[k] for k in RATINGS) / len(RATINGS) for r in rows}
    model_pos = {r["promo_id"]: i for i, r in enumerate(sorted(rows, key=lambda r: r["rank"]))}
    human_pos = {
        r["promo_id"]: i for i, r in enumerate(sorted(rows, key=lambda r: -mean[r["promo_id"]]))
    }
    by_gap = sorted(rows, key=lambda r: -abs(model_pos[r["promo_id"]] - human_pos[r["promo_id"]]))
    for r in by_gap[:5]:
        m = mean[r["promo_id"]]
        print(
            f"  rank {r['rank']:>2} ep{r['episode']} {r['start']}: human {m:.1f}/5, {r['verdict']}"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("run", type=Path)
    m.add_argument("--top", type=int, default=20)
    m.add_argument("--out", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("run", type=Path)
    c.add_argument("sheet", type=Path)
    a = ap.parse_args()
    if a.cmd == "make":
        return make(a.run, a.top, a.out or a.run.parent / f"review_sheet_{a.run.name}.csv")
    return compare(a.run, a.sheet)


if __name__ == "__main__":
    sys.exit(main())
