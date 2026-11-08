"""Verify delivered promos against an unchanged source pack, without model calls.

Usage: .venv/bin/python scripts/audit_deliverables.py SHOW_DIR RUN_DIR [--json REPORT]
Reports only counts and identifiers; never prints screenplay text.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from promo_finder.domain.models import Segment
from promo_finder.domain.timecode import parse_timecode
from promo_finder.domain.validation import validate_segments
from promo_finder.infrastructure.runs import RunArtifact
from promo_finder.ingestion import load_show


def audit(show_dir: Path, run_dir: Path) -> dict:
    show = load_show(show_dir)
    metadata = json.loads((run_dir / "run_metadata.json").read_text())
    errors = []
    if metadata["show"]["source_hash"] != show.source_hash:
        errors.append("source_hash_changed")
    if metadata["show"]["episodes"] != len(show.episodes):
        errors.append("episode_count_changed")
    variants = {}
    for path in sorted(run_dir.glob("promos*.json")):
        artifact = RunArtifact.model_validate_json(path.read_text())
        promos = artifact.promos
        variant_errors = []
        if not promos:
            variant_errors.append("no_promos")
        if [p.rank for p in promos] != list(range(1, len(promos) + 1)):
            variant_errors.append("ranks_not_contiguous")
        if len({p.id for p in promos}) != len(promos):
            variant_errors.append("duplicate_ids")
        for p in promos:
            segments = [
                Segment(
                    episode_number=s.episode,
                    start_s=s.start_s,
                    end_s=s.end_s,
                    start_beat_id="audit",
                    end_beat_id="audit",
                )
                for s in p.segments
            ]
            issues = validate_segments(show, segments)
            duration = sum(s.end_s - s.start_s for s in p.segments)
            if not math.isclose(duration, p.total_duration_s, abs_tol=0.02):
                issues.append("duration_mismatch")
            for s in p.segments:
                if (
                    abs(parse_timecode(s.start_ts) - s.start_s) > 0.011
                    or abs(parse_timecode(s.end_ts) - s.end_s) > 0.011
                ):
                    issues.append("timestamp_mismatch")
            for label, moment in (("hook", p.hook), ("cliffhanger", p.cliffhanger)):
                episode = show.episode(moment.episode)
                beat = (
                    next((b for b in episode.beats if b.id == moment.beat_id), None)
                    if episode
                    else None
                )
                if beat is None or beat.text != moment.text or beat.speaker != moment.speaker:
                    issues.append(f"{label}_source_mismatch")
                t = parse_timecode(moment.ts)
                if beat and abs(t - beat.start_s) > 0.011:
                    issues.append(f"{label}_timestamp_mismatch")
                if not any(
                    s.episode == moment.episode and s.start_s - 0.001 <= t <= s.end_s + 0.001
                    for s in p.segments
                ):
                    issues.append(f"{label}_outside_clip")
            if (
                p.hook.episode != p.segments[0].episode
                or not 0 <= parse_timecode(p.hook.ts) - p.segments[0].start_s <= 3.001
            ):
                issues.append("hook_after_first_three_seconds")
            if not p.why.strip() or not p.post_first_because.strip():
                issues.append("missing_editorial_reason")
            variant_errors.extend(f"{p.id}:{issue}" for issue in issues)
        variants[path.stem] = {
            "promos": len(promos),
            "episodes_represented": len({s.episode for p in promos for s in p.segments}),
            "min_duration_s": min((p.total_duration_s for p in promos), default=0),
            "max_duration_s": max((p.total_duration_s for p in promos), default=0),
            "needs_context": sum(
                any(r.startswith("needs context") for r in p.risks) for p in promos
            ),
            "errors": variant_errors,
        }
        errors.extend(f"{path.stem}:{issue}" for issue in variant_errors)
    required = ["promos.json", "promos.csv", "ranking.md", "quality_report.md", "run_metadata.json"]
    for name in required:
        if not (run_dir / name).is_file() or not (run_dir / name).stat().st_size:
            errors.append(f"missing:{name}")
    with (run_dir / "promos.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    main = RunArtifact.model_validate_json((run_dir / "promos.json").read_text())
    if [r["id"] for r in rows] != [p.id for p in main.promos]:
        errors.append("csv_ranking_mismatch")
    return {
        "source_episodes": len(show.episodes),
        "source_hash_matches": metadata["show"]["source_hash"] == show.source_hash,
        "run": run_dir.name,
        "provider": metadata["llm"]["provider"],
        "model": metadata["llm"]["model"],
        "partial": metadata["partial"],
        "variants": variants,
        "errors": errors,
        "ok": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("show_dir", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = audit(args.show_dir, args.run_dir)
    text = json.dumps(report, indent=2)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
