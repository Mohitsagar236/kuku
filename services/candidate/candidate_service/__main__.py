"""``python -m candidate_service <show_dir>``: candidate counts and LLM cost projection.

Prints statistics only. No script text is printed or logged.
"""

from __future__ import annotations

import argparse
import statistics
import sys

from ingestion_service import load_show
from promo_common.config import get_settings
from promo_common.logging import setup_logging

from candidate_service.config import load_config, load_lexicon
from candidate_service.generate import generate_candidates
from candidate_service.render import project

PRICE_POINTS = [(0.10, 0.40), (1.0, 5.0), (3.0, 15.0)]  # illustrative USD per 1M tokens in/out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="candidate_service")
    ap.add_argument("show_dir")
    ap.add_argument("--top-n", type=int, help="single candidates kept per episode")
    ap.add_argument("--stitch-n", type=int, help="stitched candidates kept for the whole show")
    ap.add_argument("--with-camera", action="store_true", help="include camera beats in prompts")
    ap.add_argument("-v", "--verbose", action="store_true", help="per-episode table")
    args = ap.parse_args(argv[1:])

    setup_logging("ERROR", get_settings().log_format)
    cfg = load_config()
    if args.top_n:
        cfg.top_n_per_episode = args.top_n
    if args.stitch_n:
        cfg.stitch_top_n_total = args.stitch_n
    show = load_show(args.show_dir)
    cands, st = generate_candidates(show, cfg, load_lexicon())

    print(
        f"show={show.slug} episodes={len(show.episodes)} config: top_n={cfg.top_n_per_episode} "
        f"stitch_n={cfg.stitch_top_n_total} window={cfg.min_total_s:.0f}-{cfg.max_total_s:.0f}s"
    )
    if args.verbose:
        print("\nep  dur_s beats starts ends windows after_nms kept recap_beats")
        for e in st.episodes:
            print(
                f"{e.episode:>3} {e.duration_s:>6.1f} {e.beats:>5} {e.valid_starts:>6} "
                f"{e.valid_ends:>4} {e.windows:>7} {e.after_nms:>9} {e.kept:>4} {e.recap_beats:>5}"
            )
    w = [e.windows for e in st.episodes]
    print(
        f"\nwindows/episode min={min(w)} median={statistics.median(w):.0f} max={max(w)}; "
        f"total={sum(w)}"
    )
    print(
        f"stitched: generated={st.stitched_generated} pool={st.stitched_pool} "
        f"kept={st.stitched_kept}; recap duplicates removed={st.recap_duplicates_removed}"
    )
    singles = sum(c.kind == "single" for c in cands)
    print(
        f"candidates for scoring: {len(cands)} ({singles} single, {len(cands) - singles} stitched)"
    )
    print(
        f"ending at episode end: {sum(c.signals.ends_at_episode_end for c in cands)}; "
        f"duration {min(c.duration_s for c in cands):.1f}-{max(c.duration_s for c in cands):.1f}s"
    )

    print(
        "\nProjected LLM scoring cost for this show "
        f"(camera beats {'included' if args.with_camera else 'excluded'}):"
    )
    for p in project(show, cands, include_camera=args.with_camera):
        prices = "  ".join(f"${p.cost(i, o):.2f}@{i}/{o}" for i, o in PRICE_POINTS)
        print(
            f"  {p.mode:<18} calls={p.calls:>4} in={p.tokens_in:>8,} "
            f"out={p.tokens_out:>7,}  {prices}"
        )
    print("  (price points are illustrative USD per 1M tokens in/out, not real quotes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
