"""Deterministic final validation of a promo (list of segments) against the source timeline.

Used by candidate generation tests, the ranking stage and the evaluation stage. Returns a list
of violation codes; an empty list means the promo is valid.
"""

from __future__ import annotations

from pydantic import BaseModel

from promo_finder.domain.cuts import CONTENT_TYPES, is_valid_cut, protected_spans
from promo_finder.domain.models.candidates import Segment
from promo_finder.domain.models.timeline import Show

_TOL = 1e-3


class Bounds(BaseModel):
    min_total_s: float = 20.0
    max_total_s: float = 60.0
    hook_window_s: float = 3.0
    min_segment_s: float = 3.0
    max_segments: int = 3


def validate_segments(
    show: Show, segments: list[Segment], bounds: Bounds | None = None
) -> list[str]:
    b = bounds or Bounds()
    errors: list[str] = []
    if not segments:
        return ["no_segments"]
    if len(segments) > b.max_segments:
        errors.append("too_many_segments")

    total = 0.0
    prev: Segment | None = None
    for i, seg in enumerate(segments):
        ep = show.episode(seg.episode_number)
        if ep is None:
            errors.append(f"seg{i}:unknown_episode")
            continue
        beats = ep.beats
        total += seg.duration_s
        if seg.duration_s + _TOL < b.min_segment_s and len(segments) > 1:
            errors.append(f"seg{i}:segment_too_short")
        if not any(abs(x.start_s - seg.start_s) < _TOL for x in beats):
            errors.append(f"seg{i}:start_not_on_beat")
        if not any(abs(x.end_s - seg.end_s) < _TOL for x in beats):
            errors.append(f"seg{i}:end_not_on_beat")
        spans = protected_spans(beats)
        if not is_valid_cut(seg.start_s, spans) or not is_valid_cut(seg.end_s, spans):
            errors.append(f"seg{i}:cut_inside_beat")
        if prev is not None and (seg.episode_number, seg.start_s) < (
            prev.episode_number,
            prev.end_s - _TOL,
        ):
            errors.append(f"seg{i}:out_of_order")
        prev = seg

    if not (b.min_total_s - _TOL <= total <= b.max_total_s + _TOL):
        errors.append("duration_out_of_range")

    first = segments[0]
    first_ep = show.episode(first.episode_number)
    if first_ep is not None and not any(
        x.type in CONTENT_TYPES
        and first.start_s - _TOL <= x.start_s <= first.start_s + b.hook_window_s + _TOL
        for x in first_ep.beats
    ):
        errors.append("no_hook_in_first_window")
    return errors
