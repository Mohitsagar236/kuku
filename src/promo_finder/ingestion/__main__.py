"""``python -m promo_finder.ingestion <folder>``: parse a folder and print stats only.

Prints counts and warning codes, never script text, so it is safe to run on confidential data.
"""

from __future__ import annotations

import sys
from collections import Counter

from promo_finder.infrastructure.config import get_settings
from promo_finder.infrastructure.logging import setup_logging
from promo_finder.ingestion.loader import load_show


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m promo_finder.ingestion <show_dir>", file=sys.stderr)
        return 2
    s = get_settings()
    setup_logging("ERROR", s.log_format)  # keep per-warning logs quiet; summary below
    show = load_show(argv[1])
    codes: Counter[str] = Counter()
    beats: Counter[str] = Counter()
    for ep in show.episodes:
        codes.update(w.code for w in ep.warnings)
        beats.update(b.type.value for b in ep.beats)
    codes.update(w.code for w in show.warnings)
    durs = [e.duration_s for e in show.episodes]
    print(
        f"show={show.slug} episodes={len(show.episodes)} scenes="
        f"{sum(len(e.scenes) for e in show.episodes)} beats={dict(beats)}"
    )
    print(f"duration_s min={min(durs):.1f} max={max(durs):.1f} avg={sum(durs) / len(durs):.1f}")
    print(f"warnings={dict(codes) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
