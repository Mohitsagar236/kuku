"""Short beat ids for prompts, candidate views and eligibility lists."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promo_common.cuts import CONTENT_TYPES, is_valid_cut, protected_spans
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
    end_options: list[str] = field(default_factory=list)  # legal alternative ends (singles only)
    current_end: str | None = None  # the option that is the candidate's present end, if any


def end_options(
    cand: Candidate,
    show: Show,
    ids: IdMap,
    min_total_s: float = 20.0,
    max_total_s: float = 60.0,
    spacing_s: float = 1.0,
) -> tuple[list[str], str | None]:
    """Legal alternative END beats for a single-segment candidate, start unchanged.

    A beat is offered if the cut right after it is legal (never inside dialogue/action), the beat
    is not a camera beat, and the total duration stays inside [min_total_s, max_total_s].
    Returns (short ids in time order, id of the present end if it is among them).
    """
    if cand.kind != "single":
        return [], None
    seg = cand.segments[0]
    ep = show.episode(seg.episode_number)
    assert ep is not None
    spans = protected_spans(ep.beats)
    lo, hi = seg.start_s + min_total_s - 1e-6, seg.start_s + max_total_s + 1e-6
    by_end: dict[float, Any] = {}
    for b in ep.beats:
        if b.type in CONTENT_TYPES and lo <= b.end_s <= hi and is_valid_cut(b.end_s, spans):
            key = round(b.end_s, 3)
            if key not in by_end or b.start_s >= by_end[key].start_s:
                by_end[key] = b  # several beats can end together: the last-started one is the cut
    ends = sorted(by_end)
    kept: list[float] = []
    for e in ends:
        if kept and e - kept[-1] < spacing_s and abs(kept[-1] - seg.end_s) > 1e-3:
            kept[-1] = e  # two cuts within a second are the same cut; keep the later one
        else:
            kept.append(e)
    cur_key = round(seg.end_s, 3)
    if cur_key in by_end and cur_key not in kept:
        kept = sorted(k for k in kept if abs(k - cur_key) >= spacing_s) + [cur_key]
        kept.sort()
    opts = [ids.to_short[by_end[e].id] for e in kept]
    current = ids.to_short[by_end[cur_key].id] if cur_key in by_end else None
    return opts, current


def build_view(
    cand: Candidate,
    show: Show,
    ids: IdMap,
    hook_window_s: float = 3.0,
    cliff_window_s: float = 8.0,
    min_total_s: float = 20.0,
    max_total_s: float = 60.0,
    spacing_s: float = 1.0,
    refine: bool = False,
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
    opts, current = (
        end_options(cand, show, ids, min_total_s, max_total_s, spacing_s) if refine else ([], None)
    )
    return CandView(cand, segs, hook_ok, tail, all_ids, opts, current)
