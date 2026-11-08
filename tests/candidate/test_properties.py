"""Property-based tests (hypothesis) for the candidate invariants."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from promo_finder.candidates import generate_candidates
from promo_finder.domain.cuts import CONTENT_TYPES, PROTECTED_TYPES
from promo_finder.domain.models import BeatType, Show
from promo_finder.domain.validation import validate_segments
from tests.candidate.conftest import make_episode, make_show

TEXTS = [
    "You lied to me!", "Who are you?", "The door opens slowly.", "Wait... what did you say",
    "He slaps her hard.", "Cut to black.", "I will never forgive you", "Thank you, goodbye.",
    "Run!", "It was you all along?",
]  # fmt: skip
TOL = 1e-3


@st.composite
def shows(draw) -> Show:
    eps = []
    for n in range(1, draw(st.integers(1, 3)) + 1):
        specs = []
        for _ in range(draw(st.integers(8, 45))):
            start = draw(st.integers(0, 1600)) / 10
            dur = draw(st.integers(1, 90)) / 10
            typ = draw(st.sampled_from(list(BeatType)))
            spk = (
                draw(st.sampled_from(["ANA", "BEN", "CAL", "DEE"]))
                if typ is BeatType.DIALOGUE
                else None
            )
            specs.append((typ, start, round(start + dur, 3), draw(st.sampled_from(TEXTS)), spk))
        eps.append(
            make_episode(
                n, specs, notes=draw(st.sampled_from(["", "Picks up directly from Episode 1."]))
            )
        )
    return make_show(*eps)


@settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
@given(show=shows())
def test_candidate_invariants(show: Show, cfg, lex) -> None:
    cands, _ = generate_candidates(show, cfg, lex)
    for c in cands:
        # duration within bounds, computed as the SUM of segment durations
        assert cfg.min_total_s - TOL <= c.duration_s <= cfg.max_total_s + TOL
        assert abs(c.duration_s - sum(s.end_s - s.start_s for s in c.segments)) < TOL
        assert 1 <= len(c.segments) <= cfg.max_segments
        prev = None
        for seg in c.segments:
            ep = show.episode(seg.episode_number)
            assert ep is not None
            # start < end; stitched pieces are at least min_segment_s long
            assert seg.start_s < seg.end_s
            if len(c.segments) > 1:
                assert seg.end_s - seg.start_s >= cfg.min_segment_s - TOL
            # every cut is on a real beat boundary
            assert any(abs(b.start_s - seg.start_s) < TOL for b in ep.beats)
            assert any(abs(b.end_s - seg.end_s) < TOL for b in ep.beats)
            # no mid-dialogue/action cuts, and end >= end of every such beat starting inside
            for b in ep.beats:
                if b.type in PROTECTED_TYPES:
                    assert not (b.start_s + TOL < seg.start_s < b.end_s - TOL)
                    assert not (b.start_s + TOL < seg.end_s < b.end_s - TOL)
                    if seg.start_s - TOL <= b.start_s < seg.end_s - TOL:
                        assert b.end_s <= seg.end_s + TOL
            # story order, no overlap
            if prev is not None:
                assert (prev.episode_number, prev.end_s) <= (seg.episode_number, seg.start_s + TOL)
            prev = seg
        # hook: a dialogue/action/sfx beat starts within the first 3.0 s of the promo
        first = c.segments[0]
        ep0 = show.episode(first.episode_number)
        assert ep0 is not None
        assert any(
            b.type in CONTENT_TYPES
            and first.start_s - TOL <= b.start_s <= first.start_s + 3.0 + TOL
            for b in ep0.beats
        )
        # camera beats are never the hook/cliffhanger content
        by_id = {b.id: b for e in show.episodes for b in e.beats}
        for bid in (c.signals.hook_beat_id, c.signals.cliff_beat_id):
            if bid:
                assert by_id[bid].type in CONTENT_TYPES
        # independent oracle agrees
        assert validate_segments(show, c.segments) == []
        assert 0 <= c.rule_score <= 10
