"""Short beat ids for prompts, candidate views and eligibility lists."""

from __future__ import annotations

from dataclasses import dataclass, field

from promo_common.cuts import CONTENT_TYPES
from promo_common.schemas import Beat, Candidate, Show


@dataclass
class IdMap:
    """Per-show mapping between full beat ids (E015-S01-B030) and short prompt ids (e15_b030)."""

    to_short: dict[str, str] = field(default_factory=dict)
    to_beat: dict[str, Beat] = field(default_factory=dict)
    full_to_beat: dict[str, Beat] = field(default_factory=dict)

    @classmethod
    def build(cls, show: Show) -> IdMap:
        m = cls()
        for ep in show.episodes:
            for i, b in enumerate(ep.beats):
                s = f"e{ep.number}_b{i:03d}"
                m.to_short[b.id] = s
                m.to_beat[s] = b
                m.full_to_beat[b.id] = b
        return m


@dataclass
class SegView:
    episode: int
    start: float
    end: float
    beats: list[Beat]  # content beats only (camera/other are never shown to the LLM)


@dataclass
class CandView:
    cand: Candidate
    segs: list[SegView]
    hook_ok: list[str]  # short ids
    cliff_ok: list[str]
    all_ids: set[str]


def build_view(
    cand: Candidate, show: Show, ids: IdMap, hook_window_s: float = 3.0, cliff_window_s: float = 8.0
) -> CandView:
    segs: list[SegView] = []
    for s in cand.segments:
        ep = show.episode(s.episode_number)
        assert ep is not None
        beats = [
            b
            for b in ep.beats
            if b.type in CONTENT_TYPES and s.start_s - 1e-6 <= b.start_s < s.end_s - 1e-6
        ]
        segs.append(SegView(s.episode_number, s.start_s, s.end_s, beats))
    first, last = segs[0], segs[-1]
    hook_ok = [
        ids.to_short[b.id] for b in first.beats if b.start_s <= first.start + hook_window_s + 1e-6
    ]
    tail = [ids.to_short[b.id] for b in last.beats if b.end_s >= last.end - cliff_window_s]
    if not tail and last.beats:
        tail = [ids.to_short[max(last.beats, key=lambda b: (b.end_s, b.start_s)).id]]
    all_ids = {ids.to_short[b.id] for sv in segs for b in sv.beats}
    return CandView(cand, segs, hook_ok, tail, all_ids)
