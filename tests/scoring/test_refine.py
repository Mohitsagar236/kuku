"""End-point refinement: the LLM may move a clip's END (start fixed) to the strongest unresolved cut."""

from __future__ import annotations

import pytest

from promo_finder.domain.cuts import CONTENT_TYPES, is_valid_cut, protected_spans
from promo_finder.domain.models import Candidate, RuleSignals, Segment
from promo_finder.domain.validation import validate_segments
from promo_finder.ranking import load_ranking_config, rank
from promo_finder.scoring import Scorer, load_scoring_config
from promo_finder.scoring.ids import IdMap, build_view, end_options
from tests.candidate.conftest import D, line_text, make_episode, make_show
from tests.shared import SpyMock, make_llm

# A fixture shaped like the real ep3 case: the clip starts at 0.0 and presently ends on a calm
# check-in ("Are you hurt?") at 31.5; a sharper shout sits at 28.0 and a line cut off mid-sentence
# sits at 40.8, both inside the legal 20-60 s window.
SHOUT_END, PRESENT_END, CUTOFF_END = 28.0, 31.5, 40.8


def story_show():
    specs = [
        (D, i * 3.5, i * 3.5 + 2.5, line_text(i, "ref"), "ANA" if i % 2 else "BEN")
        for i in range(7)
    ]
    specs += [
        (D, 25.5, SHOUT_END, "Lock every gate now!", "BEN"),
        (D, 29.0, PRESENT_END, "Are you hurt?", "CAL"),
        (D, 32.5, 35.0, line_text(11, "ref"), "ANA"),
        (D, 35.5, 37.5, line_text(12, "ref"), "BEN"),
        (D, 38.0, CUTOFF_END, "I can...", "CAL"),
        (D, 42.0, 44.5, line_text(13, "ref"), "ANA"),
        (D, 46.0, 48.5, line_text(14, "ref"), "BEN"),
    ]
    return make_show(make_episode(1, specs))


def candidate(show, end: float) -> Candidate:
    beats = show.episode(1).beats
    sb = beats[0]
    eb = [b for b in beats if abs(b.end_s - end) < 1e-6][-1]
    seg = Segment(episode_number=1, start_s=0.0, end_s=end, start_beat_id=sb.id, end_beat_id=eb.id)
    sig = RuleSignals(
        hook=6, cliffhanger=4, intensity=5, clarity=5, hook_beat_id=sb.id, cliff_beat_id=eb.id
    )
    return Candidate(id="shape", kind="single", segments=[seg], duration_s=end, rule_score=5,
                     signals=sig, content_hash="0" * 64)  # fmt: skip


@pytest.fixture()
def story():
    return story_show()


@pytest.fixture()
def scfg():
    return load_scoring_config()


def test_end_options_are_legal_non_camera_and_include_the_present_end(story) -> None:
    ids = IdMap.build(story)
    cand = candidate(story, PRESENT_END)
    opts, current = end_options(cand, story, ids)
    beats = story.episode(1).beats
    spans = protected_spans(beats)
    ends = [ids.to_beat[o].end_s for o in opts]
    assert ends == sorted(ends) and SHOUT_END in ends and PRESENT_END in ends and CUTOFF_END in ends
    assert current is not None and ids.to_beat[current].end_s == PRESENT_END
    for o in opts:
        b = ids.to_beat[o]
        assert b.type in CONTENT_TYPES and is_valid_cut(b.end_s, spans)
        assert (
            20.0 - 1e-6 <= b.end_s - cand.segments[0].start_s <= 60.0 + 1e-6
        )  # total stays 20-60 s
    assert all(
        b - a >= 1.0 for a, b in zip(ends, ends[1:], strict=False)
    )  # near-duplicate cuts thinned


def test_stitched_candidates_get_no_end_options(story) -> None:
    c = candidate(story, PRESENT_END).model_copy(update={"kind": "stitched"})
    assert end_options(c, story, IdMap.build(story)) == ([], None)


def test_blind_refine_prompt_shows_options_marker_and_no_context(story, scfg) -> None:
    story.episodes[0].logline = "ZZLOGLINE secret backstory"
    story.episodes[0].continuity_notes = "ZZNOTES"
    spy = SpyMock()
    Scorer(make_llm(spy), story, scfg).score([candidate(story, PRESENT_END)])
    req = next(r for r in spy.requests if r.task == "clarity")
    text = req.system + req.user
    assert "LEGAL END POINTS:" in req.user and "<== present end" in req.user
    assert "ZZLOGLINE" not in text and "ZZNOTES" not in text  # still blind to the story


def test_llm_moves_the_end_to_the_cutoff_line_and_code_recomputes_the_duration(story, scfg) -> None:
    cand = candidate(story, PRESENT_END)
    scores = Scorer(make_llm(), story, scfg).score([cand])
    s = scores[cand.id]
    end_beat = next(b for b in story.episode(1).beats if abs(b.end_s - CUTOFF_END) < 1e-6)
    assert s.end_changed and s.end_beat_id == end_beat.id and s.end_cliff is not None

    scores[cand.id] = s.model_copy(update={"hook": 8.0})  # pin the mock's noise: not under test
    res = rank(story, [cand], scores, load_ranking_config())
    assert len(res.promos) == 1
    p = res.promos[0]
    seg = p.segments[0]
    assert (seg.start_s, seg.end_s) == (0.0, CUTOFF_END)  # start kept, end moved
    assert p.total_duration_s == CUTOFF_END and 20 <= p.total_duration_s <= 60  # recomputed by code
    assert (
        p.end_refined and p.original_end_ts == "00:31.50" and p.original_duration_s == PRESENT_END
    )
    assert p.cliffhanger.text == "I can..." and p.cliffhanger.ts.startswith("00:38")
    first_beat = story.episode(1).beats[0]
    assert p.hook.beat_id == first_beat.id  # hook untouched
    seg_obj = Segment(
        episode_number=1, start_s=seg.start_s, end_s=seg.end_s, start_beat_id="x", end_beat_id="y"
    )
    assert validate_segments(story, [seg_obj]) == []
    assert res.final_stats.ends_refined == 1
    # the reported LLM cliffhanger is the DELIVERED end's score, not the old end's
    assert p.scores.llm_cliffhanger == scores[cand.id].end_cliff != scores[cand.id].cliffhanger


def test_keeps_the_present_end_when_it_is_already_the_best(story, scfg) -> None:
    cand = candidate(story, CUTOFF_END)  # already ends on the cut-off line
    s = Scorer(make_llm(), story, scfg).score([cand])[cand.id]
    assert not s.end_changed
    s = s.model_copy(
        update={"hook": 8.0, "cliffhanger": 8.0, "clarity": 8.0}
    )  # pin the mock's noise
    p = rank(story, [cand], {cand.id: s}, load_ranking_config()).promos[0]
    assert not p.end_refined and p.total_duration_s == CUTOFF_END


def test_an_end_id_outside_the_legal_list_is_rejected_and_retried(story, scfg) -> None:
    spy = SpyMock(faults=["ok", "bad_end"])  # 1st call is the score call, 2nd is clarity
    cand = candidate(story, PRESENT_END)
    out = Scorer(make_llm(spy), story, scfg).score([cand])
    assert out[cand.id].clarity_ok and out[cand.id].end_beat_id is not None  # recovered on retry
    retry = [r for r in spy.requests if r.task == "clarity" and "REJECTED" in r.user]
    assert len(retry) == 1 and "legal end points" in retry[0].user


def test_refinement_adds_no_calls_over_plain_clarity(show, cands, scfg) -> None:
    """The end choice rides inside the existing blind-clarity call."""
    import math

    spy = SpyMock()
    Scorer(make_llm(spy), show, scfg).score(cands)
    clarity_calls = [r for r in spy.requests if r.task == "clarity"]
    assert len(clarity_calls) == math.ceil(len(cands) / scfg.clarity_batch)


def test_view_has_options_only_when_refinement_is_on(story, scfg) -> None:
    ids = IdMap.build(story)
    cand = candidate(story, PRESENT_END)
    assert build_view(cand, story, ids, refine=False).end_options == []
    assert build_view(cand, story, ids, refine=True).end_options
