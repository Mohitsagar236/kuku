"""Cut-point rules shared by candidate generation and final validation.

A cut at time ``t`` is *valid* if it does not fall strictly inside any dialogue or action beat.
Camera and sound-FX beats are not protected: a clip may start or end at their boundaries.
Because beats overlap, validity is checked against ALL protected beats, so an end cut is
automatically >= the end of every dialogue/action beat that started inside the window.
"""

from __future__ import annotations

from collections.abc import Iterable

from promo_finder.domain.models.timeline import Beat, BeatType

CONTENT_TYPES = frozenset({BeatType.DIALOGUE, BeatType.ACTION, BeatType.SOUND_FX})
PROTECTED_TYPES = frozenset({BeatType.DIALOGUE, BeatType.ACTION})
EPS = 1e-6


def protected_spans(beats: Iterable[Beat]) -> list[tuple[float, float]]:
    return [
        (b.start_s, b.end_s) for b in beats if b.type in PROTECTED_TYPES and b.end_s > b.start_s
    ]


def is_valid_cut(t: float, spans: list[tuple[float, float]]) -> bool:
    return not any(s + EPS < t < e - EPS for s, e in spans)
