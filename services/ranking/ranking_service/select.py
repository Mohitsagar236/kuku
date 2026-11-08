"""Dedupe overlapping promos and enforce diversity across scenes/episodes/arcs."""

from __future__ import annotations

from collections import Counter

from promo_common.schemas import Candidate, Segment, Show
from promo_common.timecode import overlap

from ranking_service.config import Selection
from ranking_service.fusion import Fused


def candidate_iou(a: Candidate, b: Candidate) -> float:
    ov = sum(
        overlap(x.start_s, x.end_s, y.start_s, y.end_s)
        for x in a.segments
        for y in b.segments
        if x.episode_number == y.episode_number
    )
    union = a.duration_s + b.duration_s - ov
    return ov / union if union > 0 else 0.0


def same_ending(a: Candidate, b: Candidate, tol_s: float) -> bool:
    """Promos cutting at the same cliffhanger moment are redundant, even with little overlap."""
    x, y = a.segments[-1], b.segments[-1]
    return x.episode_number == y.episode_number and abs(x.end_s - y.end_s) < tol_s


def segments_redundant(
    a: list[Segment], b: list[Segment], iou_limit: float, same_end_tol_s: float
) -> str | None:
    """Why two DELIVERED cuts are redundant ('overlap' / 'same_ending'), else None.

    Same thresholds as ``select``, but on the final segments: end refinement can move a cut
    onto another promo's ending after selection has already de-duplicated the candidates.
    """
    ov = sum(
        overlap(x.start_s, x.end_s, y.start_s, y.end_s)
        for x in a
        for y in b
        if x.episode_number == y.episode_number
    )
    union = sum(x.end_s - x.start_s for x in a) + sum(y.end_s - y.start_s for y in b) - ov
    if union > 0 and ov / union > iou_limit:
        return "overlap"
    x, y = a[-1], b[-1]
    if x.episode_number == y.episode_number and abs(x.end_s - y.end_s) < same_end_tol_s:
        return "same_ending"
    return None


def scene_of(show: Show, cand: Candidate) -> tuple[int, int]:
    seg = cand.segments[0]
    ep = show.episode(seg.episode_number)
    assert ep is not None
    beat = next((b for b in ep.beats if b.id == seg.start_beat_id), None)
    return seg.episode_number, beat.scene_idx if beat else 0


def select(
    fused: list[Fused], show: Show, cfg: Selection, limit: int | None = None
) -> tuple[list[Fused], Counter[str]]:
    """Greedy by score. Returns (selected, counts of why others were dropped)."""
    limit = limit or cfg.output_count
    kept: list[Fused] = []
    dropped: Counter[str] = Counter()
    per_ep: Counter[int] = Counter()
    per_scene: Counter[tuple[int, int]] = Counter()
    per_arc: Counter[int] = Counter()
    for f in sorted(fused, key=lambda f: (-f.final, f.cand.id)):
        c = f.cand
        ep = c.segments[0].episode_number
        scene = scene_of(show, c)
        arc = (ep - 1) // cfg.arc_size
        if any(candidate_iou(c, k.cand) > cfg.dedupe_iou for k in kept):
            dropped["overlap"] += 1
        elif any(same_ending(c, k.cand, cfg.same_ending_tol_s) for k in kept):
            dropped["same_ending"] += 1
        elif per_ep[ep] >= cfg.max_per_episode:
            dropped["episode_cap"] += 1
        elif per_scene[scene] >= cfg.max_per_scene:
            dropped["scene_cap"] += 1
        elif per_arc[arc] >= cfg.max_per_arc:
            dropped["arc_cap"] += 1
        else:
            kept.append(f)
            per_ep[ep] += 1
            per_scene[scene] += 1
            per_arc[arc] += 1
            if len(kept) >= limit:
                break
    return kept, dropped
