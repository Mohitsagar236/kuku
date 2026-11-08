"""Render candidates as prompt text and project LLM cost. Used by scoring (Phase 3) too."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from promo_finder.domain.models import Beat, BeatType, Candidate, Show
from promo_finder.domain.timecode import format_timecode

PROMPT_OVERHEAD_TOKENS = 500  # instructions + JSON schema, per call
OUT_TOKENS_PER_CANDIDATE = 120  # strict JSON verdict
RANGE_LINE_TOKENS = 25
STITCH_BATCH = 5


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def render_beat(b: Beat) -> str:
    who = ""
    if b.type is BeatType.DIALOGUE:
        tags = f" ({', '.join(b.emotion_tags)})" if b.emotion_tags else ""
        who = f"{b.speaker}{tags}: "
    elif b.type is not BeatType.OTHER:
        who = f"[{b.type.value}] "
    return f"[{b.id} {format_timecode(b.start_s)}] {who}{b.text}"


def render_beats(beats: list[Beat], include_camera: bool) -> str:
    return "\n".join(
        render_beat(b) for b in beats if include_camera or b.type is not BeatType.CAMERA
    )


def render_candidate(c: Candidate, show: Show, include_camera: bool = False) -> str:
    parts = []
    for i, seg in enumerate(c.segments, 1):
        ep = show.episode(seg.episode_number)
        assert ep is not None
        beats = [b for b in ep.beats if seg.start_s - 1e-6 <= b.start_s < seg.end_s - 1e-6]
        head = (
            f"-- segment {i}: episode {seg.episode_number}, "
            f"{format_timecode(seg.start_s)}-{format_timecode(seg.end_s)}"
        )
        parts.append(head + "\n" + render_beats(beats, include_camera))
    return "\n".join(parts)


@dataclass
class Projection:
    mode: str
    calls: int
    tokens_in: int
    tokens_out: int

    def cost(self, price_in: float, price_out: float) -> float:
        """Prices are USD per 1M tokens."""
        return (self.tokens_in * price_in + self.tokens_out * price_out) / 1e6


def _ctx_tokens(show: Show, episodes: list[int]) -> int:
    total = 0
    for n in episodes:
        ep = show.episode(n)
        if ep:
            total += estimate_tokens(ep.logline + " " + ep.continuity_notes)
    return total


def project(
    show: Show, candidates: list[Candidate], include_camera: bool = False
) -> list[Projection]:
    """Project LLM calls/tokens for scoring ``candidates``.

    * per_candidate: one call per candidate.
    * per_episode: one call per episode scoring all its single candidates; overlapping windows
      share beats, so each beat is sent once. Stitched candidates go in batches of 5.
    """
    out_per = OUT_TOKENS_PER_CANDIDATE

    def ctx(c: Candidate) -> int:
        return _ctx_tokens(show, c.episodes)

    pc_in = sum(
        PROMPT_OVERHEAD_TOKENS + ctx(c) + estimate_tokens(render_candidate(c, show, include_camera))
        for c in candidates
    )
    per_cand = Projection("per_candidate", len(candidates), pc_in, out_per * len(candidates))

    by_ep: dict[int, list[Candidate]] = defaultdict(list)
    stitched: list[Candidate] = []
    for c in candidates:
        (by_ep[c.segments[0].episode_number] if c.kind == "single" else stitched).append(c)
    calls = tokens_in = tokens_out = 0
    for n, cs in by_ep.items():
        ep = show.episode(n)
        assert ep is not None
        covered = {
            b.id: b
            for c in cs
            for b in ep.beats
            if c.segments[0].start_s - 1e-6 <= b.start_s < c.segments[0].end_s - 1e-6
        }
        text = render_beats(
            sorted(covered.values(), key=lambda b: (b.start_s, b.id)), include_camera
        )
        calls += 1
        tokens_in += (
            PROMPT_OVERHEAD_TOKENS
            + _ctx_tokens(show, [n])
            + estimate_tokens(text)
            + RANGE_LINE_TOKENS * len(cs)
        )
        tokens_out += out_per * len(cs)
    for i in range(0, len(stitched), STITCH_BATCH):
        batch = stitched[i : i + STITCH_BATCH]
        calls += 1
        eps = sorted({e for c in batch for e in c.episodes})
        tokens_in += (
            PROMPT_OVERHEAD_TOKENS
            + _ctx_tokens(show, eps)
            + sum(estimate_tokens(render_candidate(c, show, include_camera)) for c in batch)
        )
        tokens_out += out_per * len(batch)
    return [per_cand, Projection("per_episode_batch", calls, tokens_in, tokens_out)]
