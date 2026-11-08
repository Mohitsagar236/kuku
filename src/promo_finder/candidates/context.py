"""Per-episode index used by all candidate stages."""

from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass, field

from promo_finder.candidates.config import CandidateConfig
from promo_finder.domain.cuts import CONTENT_TYPES, EPS, is_valid_cut, protected_spans
from promo_finder.domain.models import Beat, Episode

_CONTINUITY = re.compile(
    r"\b(picks up|pick up|continues|continuing|immediately (?:after|following)|"
    r"directly (?:from|after|following)|right after)\b",
    re.I,
)


@dataclass
class EpCtx:
    ep: Episode
    cfg: CandidateConfig
    is_first: bool = False
    recap: dict[str, str] = field(default_factory=dict)  # beat id -> canonical (source) text
    beats: list[Beat] = field(init=False)
    starts: list[float] = field(init=False)
    ep_end: float = field(init=False)
    spans: list[tuple[float, float]] = field(init=False)
    valid_starts: list[float] = field(init=False)
    valid_ends: list[float] = field(init=False)
    continuity_dependent: bool = field(init=False)

    def __post_init__(self) -> None:
        self.beats = self.ep.beats
        self.starts = [b.start_s for b in self.beats]
        self.ep_end = max((b.end_s for b in self.beats), default=0.0)
        self.spans = protected_spans(self.beats)
        self.continuity_dependent = bool(_CONTINUITY.search(self.ep.continuity_notes))
        hw = self.cfg.hook_window_s
        content_starts = [b.start_s for b in self.beats if b.type in CONTENT_TYPES]
        self.valid_starts = sorted(
            {
                t
                for t in self.starts
                if is_valid_cut(t, self.spans)
                and any(t - EPS <= c <= t + hw + EPS for c in content_starts)
            }
        )
        self.valid_ends = sorted(
            {b.end_s for b in self.beats if b.end_s > 0 and is_valid_cut(b.end_s, self.spans)}
        )

    def beats_in(self, start: float, end: float) -> list[Beat]:
        """Beats whose start lies in [start, end)."""
        lo = bisect_left(self.starts, start - EPS)
        hi = bisect_left(self.starts, end - EPS)
        return self.beats[lo:hi]

    def scene_start(self, t: float) -> float:
        starts = [s.start_s for s in self.ep.scenes if s.start_s <= t + EPS]
        return max(starts) if starts else 0.0

    def boundary_ids(self, start: float, end: float) -> tuple[str, str]:
        first = next(b for b in self.beats if abs(b.start_s - start) < 1e-3)
        last = [b for b in self.beats if abs(b.end_s - end) < 1e-3][-1]
        return first.id, last.id
