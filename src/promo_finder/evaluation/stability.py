"""Score stability: re-score a sample a second time and measure how much scores move."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from promo_finder.domain.models import LLMScores

DIMS = ("hook", "cliffhanger", "intensity", "spoiler_risk", "clarity")


@dataclass
class DimStability:
    n: int
    mean_abs_diff: float
    std_diff: float
    pct_identical: float
    max_abs_diff: float


@dataclass
class StabilityReport:
    n_sampled: int
    n_compared: int
    per_dim: dict[str, DimStability] = field(default_factory=dict)
    top10_changed: int | None = None
    top10_rescored: int = 0
    seed: int = 0
    note: str = ""


def compare_scores(a: dict[str, LLMScores], b: dict[str, LLMScores]) -> dict[str, DimStability]:
    out: dict[str, DimStability] = {}
    for dim in DIMS:
        diffs = []
        for cid, sa in a.items():
            sb = b.get(cid)
            if sb is None:
                continue
            va, vb = getattr(sa, dim), getattr(sb, dim)
            if va is not None and vb is not None:
                diffs.append(vb - va)
        if not diffs:
            continue
        absd = [abs(d) for d in diffs]
        out[dim] = DimStability(
            n=len(diffs),
            mean_abs_diff=round(sum(absd) / len(absd), 3),
            std_diff=round(statistics.pstdev(diffs), 3) if len(diffs) > 1 else 0.0,
            pct_identical=round(100 * sum(1 for d in absd if d == 0) / len(absd), 1),
            max_abs_diff=max(absd),
        )
    return out
