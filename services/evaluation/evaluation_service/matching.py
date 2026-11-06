"""Time-range matching between promos (used by golden set, ablation and stability)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from promo_common.timecode import overlap


class SegLike(Protocol):
    episode: int
    start_s: float
    end_s: float


def promo_iou(a: Sequence[SegLike], b: Sequence[SegLike]) -> float:
    ov = sum(
        overlap(x.start_s, x.end_s, y.start_s, y.end_s)
        for x in a
        for y in b
        if x.episode == y.episode
    )
    da = sum(x.end_s - x.start_s for x in a)
    db = sum(y.end_s - y.start_s for y in b)
    union = da + db - ov
    return ov / union if union > 0 else 0.0


def topk_overlap(
    a: Sequence[Sequence[SegLike]], b: Sequence[Sequence[SegLike]], thr: float = 0.5
) -> int:
    """How many promos in ``a`` have a counterpart (IoU >= thr) in ``b``."""
    return sum(1 for x in a if any(promo_iou(x, y) >= thr for y in b))
