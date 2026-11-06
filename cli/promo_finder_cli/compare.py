"""`promo-finder compare RUN_A RUN_B`: side-by-side of two smoke runs (e.g. two models)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _load(d: Path) -> dict[str, Any]:
    f = d / "smoke_summary.json"
    if not f.is_file():
        raise FileNotFoundError(f"{f} not found: run `make smoke` for that model first")
    data: dict[str, Any] = json.loads(f.read_text())
    return data


def _seg(p: dict[str, Any]) -> str:
    return " + ".join(f"ep{s['episode']} {s['start_ts']}-{s['end_ts']}" for s in p["segments"])


def _moment(m: dict[str, Any]) -> str:
    who = f"{m['speaker']}: " if m.get("speaker") else f"[{m['kind']}] "
    return f'{m["ts"]} {who}"{m["text"]}"'


def compare_runs(a_dir: Path, b_dir: Path) -> tuple[str, Path]:
    a, b = _load(a_dir), _load(b_dir)
    rows = [
        ("model", a["model"], b["model"]),
        ("episodes / candidates / fully scored", f"{a['episodes']} / {a['candidates']} / {a['fully_scored']}",
         f"{b['episodes']} / {b['candidates']} / {b['fully_scored']}"),
        ("LLM calls (by task)", f"{a['calls']} {a['calls_by_task']}", f"{b['calls']} {b['calls_by_task']}"),
        ("tokens in / out", f"{a['tokens_in']:,} / {a['tokens_out']:,}", f"{b['tokens_in']:,} / {b['tokens_out']:,}"),
        ("scoring output tokens per candidate", a["score_out_tokens_per_candidate"], b["score_out_tokens_per_candidate"]),
        ("latency per call mean / p95 (ms)", f"{a['latency_ms']['mean']:.0f} / {a['latency_ms']['p95']:.0f}",
         f"{b['latency_ms']['mean']:.0f} / {b['latency_ms']['p95']:.0f}"),
        ("unparseable replies", a["invalid_json"], b["invalid_json"]),
        ("candidate answers rejected", a["rejected"], b["rejected"]),
        ("extra retry calls (validation)", a["retry_calls"], b["retry_calls"]),
        ("still failing after retries", a["still_failing"], b["still_failing"]),
        ("truncated responses", a["truncated"], b["truncated"]),
        ("transient retries by kind", a["transient_retries"] or "none", b["transient_retries"] or "none"),
        ("gated before stage 2 (hook) / after", f"{a['gates']['first_pass_hook']} / {a['gates']['after'] or 0}",
         f"{b['gates']['first_pass_hook']} / {b['gates']['after'] or 0}"),
        ("ends moved by refinement / rejected", f"{a['ends_refined']} / {a['ends_refine_rejected']}",
         f"{b['ends_refined']} / {b['ends_refine_rejected']}"),
        ("promos delivered", a["promos"], b["promos"]),
    ]  # fmt: skip
    L = ["# Model comparison (smoke)", "", "| measure | A | B |", "|---|---|---|"]
    L += [f"| {k} | {x} | {y} |" for k, x, y in rows]
    for label, s in (("A", a), ("B", b)):
        L += ["", f"## Top 3: {label} = {s['model']}"]
        for p in s["top3"]:
            sc = p["scores"]
            L += [
                "",
                f"**#{p['rank']}** {_seg(p)} ({p['total_duration_s']:.0f}s)"
                + (
                    "  [END MOVED from " + str(p["original_end_ts"]) + "]"
                    if p.get("end_refined")
                    else ""
                ),
                f"- hook {_moment(p['hook'])}",
                f"- cliffhanger {_moment(p['cliffhanger'])}",
                f"- scores: hook {sc['hook']} (LLM {sc['llm_hook']}), cliffhanger {sc['cliffhanger']} "
                f"(LLM {sc['llm_cliffhanger']}), clarity {sc['clarity']}, intensity {sc['intensity']}, "
                f"spoiler {sc['spoiler_risk']}",
                f"- why: {p['why']}",
            ]
    text = "\n".join(L)
    dest = a_dir.parent / f"model_comparison-{time.strftime('%Y%m%d-%H%M%S')}.md"
    dest.write_text(text)
    return text, dest
