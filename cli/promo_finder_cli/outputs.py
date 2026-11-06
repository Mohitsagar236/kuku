"""Write promos.json/csv, ranking.md, quality_report.md, run_metadata.json."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from evaluation_service import AblationInfo, ReportInput, build_report
from promo_common.schemas import Promo

from promo_finder_cli.pipeline import BASELINE_DIR, RunResult, Variant

CONFIDENTIAL_NOTE = (
    "CONFIDENTIAL: these files quote Kuku's screenplay text.\n"
    "Do not commit them or publish them. Share only through the private submission link.\n"
)


def _promo_dict(p: Promo) -> dict[str, Any]:
    return p.model_dump(mode="json")


def write_json(
    path: Path, promos: list[Promo], run: RunResult, variant: str, model: str | None = None
) -> None:
    doc = {
        "show": run.show.title,
        "variant": variant,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": model or f"{run.llm.provider}/{run.llm.model}",
        "partial": run.partial if variant == "main" else False,
        "promos": [_promo_dict(p) for p in promos],
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False))


def write_csv(path: Path, promos: list[Promo]) -> None:
    cols = ["rank", "id", "kind", "confidence", "final_score", "rule_score", "total_duration_s"]
    for i in (1, 2, 3):
        cols += [f"seg{i}_episode", f"seg{i}_start", f"seg{i}_end"]
    cols += ["hook_ts", "hook_speaker", "hook_text", "cliffhanger_ts", "cliffhanger_speaker",
             "cliffhanger_text", "why", "hook_score", "cliffhanger_score", "clarity_score",
             "intensity_score", "spoiler_risk", "coherence", "risks", "flags", "trim_applied"]  # fmt: skip
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for p in promos:
            row: dict[str, Any] = {
                "rank": p.rank, "id": p.id, "kind": p.kind, "confidence": p.confidence,
                "final_score": p.final_score, "rule_score": p.rule_score,
                "total_duration_s": p.total_duration_s,
                "hook_ts": p.hook.ts, "hook_speaker": p.hook.speaker or "", "hook_text": p.hook.text,
                "cliffhanger_ts": p.cliffhanger.ts, "cliffhanger_speaker": p.cliffhanger.speaker or "",
                "cliffhanger_text": p.cliffhanger.text, "why": p.why,
                "hook_score": p.scores.hook, "cliffhanger_score": p.scores.cliffhanger,
                "clarity_score": p.scores.clarity, "intensity_score": p.scores.intensity,
                "spoiler_risk": p.scores.spoiler_risk, "coherence": p.scores.coherence,
                "risks": " | ".join(p.risks), "flags": " | ".join(p.flags), "trim_applied": p.trim_applied,
            }  # fmt: skip
            for i, s in enumerate(p.segments, start=1):
                row[f"seg{i}_episode"], row[f"seg{i}_start"], row[f"seg{i}_end"] = (
                    s.episode,
                    s.start_ts,
                    s.end_ts,
                )
            w.writerow(row)


def _moment(label: str, m: Any) -> str:
    who = f"{m.speaker}: " if m.speaker else f"[{m.kind}] "
    return f'- **{label}** (ep{m.episode} {m.ts}): {who}"{m.text}"'


def write_ranking_md(
    path: Path, promos: list[Promo], title: str, scored_by: str, note: str = ""
) -> None:
    L = [f"# Top 10 promos: {title}", "", f"Scored by {scored_by}. {note}".strip(),
         "Timestamps are per episode (mm:ss.xx).", ""]  # fmt: skip
    for p in promos[:10]:
        segs = " + ".join(f"ep{s.episode} {s.start_ts} to {s.end_ts}" for s in p.segments)
        L += [f"## {p.rank}. {segs}  ({p.total_duration_s:.0f}s, {p.kind})", "",
              _moment("Hook", p.hook), _moment("Cliffhanger", p.cliffhanger),
              f"- **Why it works**: {p.why}", f"- **{p.post_first_because}**",
              f"- Scores: hook {p.scores.hook:.1f}, cliffhanger {p.scores.cliffhanger:.1f}, "
              f"clarity {p.scores.clarity:.1f}, intensity {p.scores.intensity:.1f}; "
              f"confidence {p.confidence:.2f}",
              f"- Risks: {'; '.join(p.risks) if p.risks else 'none flagged'}", ""]  # fmt: skip
    path.write_text("\n".join(L))


def _variant_info(v: Variant | None) -> dict[str, Any] | None:
    if v is None:
        return None
    return {
        "candidates": len(v.cands), "promos": len(v.result.promos),
        "fully_scored": v.info.get("ranked_candidates"), "dropped": dict(v.result.dropped),
        "top10_end_at_episode_end": sum(p.ends_at_episode_end for p in v.result.promos[:10]),
    }  # fmt: skip


def write_baseline(run: RunResult) -> Path | None:
    """outputs/<show>/rules_baseline/: the rules-only run, kept next to the LLM runs (gitignored)."""
    if run.baseline is None or not run.opts.write_baseline:
        return None
    d = run.out_dir.parent / BASELINE_DIR
    d.mkdir(parents=True, exist_ok=True)
    promos = run.baseline.result.promos
    write_json(d / "promos.json", promos, run, "rules_baseline", model="rules-only (no LLM)")
    write_csv(d / "promos.csv", promos)
    write_ranking_md(d / "ranking.md", promos, run.show.title, "rules only (no LLM)",
                     "Free baseline: candidate rules + rule scores, same ranking and validation.")  # fmt: skip
    (d / "CONFIDENTIAL.txt").write_text(CONFIDENTIAL_NOTE)
    return d


def write_all(run: RunResult) -> Path:
    out = run.out_dir
    out.mkdir(parents=True, exist_ok=True)
    main = run.main
    scored_by = f"{run.llm.provider}/{run.llm.model}"
    write_json(out / "promos.json", main.result.promos, run, "main")
    write_csv(out / "promos.csv", main.result.promos)
    write_ranking_md(out / "ranking.md", main.result.promos, run.show.title, scored_by,
                     "Ranked by blended rule + LLM score with a cross-show rerank of the shortlist.")  # fmt: skip
    for spec, v in run.ablations:
        write_json(
            out / f"promos_ablation_{spec.name}.json", v.result.promos, run, f"ablation:{spec.name}"
        )
    (out / "CONFIDENTIAL.txt").write_text(CONFIDENTIAL_NOTE)
    baseline_dir = write_baseline(run)

    summary = run.llm.summary()
    st = main.scorer.stats
    main_score_out = sum(
        r.tokens_out for r in run.llm.records if r.phase == "main" and r.task == "score"
    )
    live = max(1, st.get("score_live", 0))
    report = build_report(ReportInput(
        show=run.show, cands=main.cands, scores=main.scores, result=main.result,
        scorer_stats=dict(st), llm_summary=summary,
        gen_summary={"windows": main.gen.total_windows},
        ablations=[AblationInfo(s.name, s.label, v.result, v.info) for s, v in run.ablations],
        baseline=run.baseline.result if run.baseline else None,
        cascade={"stage2": main.info.get("ranked_candidates")},
        stability=run.stability, golden=run.golden, partial=run.partial,
    ))  # fmt: skip
    (out / "quality_report.md").write_text(report)

    meta = {
        "run_finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": run.opts.mode,
        "episodes_requested": sorted(run.opts.episodes) if run.opts.episodes else "all",
        "duration_s": round(run.duration_s, 1),
        "show": {"title": run.show.title, "slug": run.show.slug, "source_hash": run.show.source_hash,
                 "episodes": len(run.show.episodes), "speaker_aliases": run.show.speaker_aliases},  # fmt: skip
        "llm": {"provider": run.llm.provider, "model": run.llm.model,
                "temperature": run.llm.temperature, "rpm": run.llm_settings.rpm,
                "rpd": run.llm_settings.rpd, "max_concurrency": run.llm_settings.max_concurrency,
                "budget_usd": run.llm_settings.budget_usd},  # fmt: skip
        "prompts": main.scorer.prompt_versions(),
        "config": run.configs,
        "usage": summary,
        "calls_by_phase": summary["by_phase"],
        "truncated_calls": summary["truncated_calls"],
        "measured_output_tokens_per_scored_candidate": round(main_score_out / live, 1),
        "scoring_stats": dict(st),
        "partial": run.partial,
        "cascade": {"top": run.configs["scoring"]["cascade_top"],
                    "fully_scored": main.info.get("ranked_candidates"),
                    "first_pass_scored": len(main.cands)},  # fmt: skip
        "variants": {
            "main": _variant_info(main),
            "rules_baseline": _variant_info(run.baseline),
            **{f"ablation:{s.name}": _variant_info(v) for s, v in run.ablations},
        },
        "rules_baseline_dir": str(baseline_dir) if baseline_dir else None,
        "stability": None if not run.stability else {
            "n_sampled": run.stability.n_sampled, "top10_changed": run.stability.top10_changed},
        "golden": None if not run.golden else {
            "precision_at_k": run.golden.precision_at_k, "n": run.golden.n_golden},
    }  # fmt: skip
    (out / "run_metadata.json").write_text(json.dumps(meta, indent=2, default=str))
    return out
