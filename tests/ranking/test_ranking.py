from __future__ import annotations

import pytest

from promo_finder.domain.models import Candidate, LLMScores, RuleSignals, Segment
from promo_finder.domain.validation import validate_segments
from promo_finder.ranking import load_ranking_config, rank
from promo_finder.ranking.build import finalize
from promo_finder.ranking.fusion import fuse
from promo_finder.ranking.rank import apply_rerank
from promo_finder.ranking.select import candidate_iou, same_ending, select
from tests.shared import SpyMock, make_llm  # noqa: F401  (fixtures: show, cands)


@pytest.fixture()
def rcfg():
    return load_ranking_config()


def mk(
    show, ep: int, s: float, e: float, score: float = 5.0, kind: str = "single", **sig
) -> Candidate:
    beats = show.episode(ep).beats
    sb = next(b for b in beats if abs(b.start_s - s) < 1e-6)
    eb = [b for b in beats if abs(b.end_s - e) < 1e-6][-1]
    seg = Segment(episode_number=ep, start_s=s, end_s=e, start_beat_id=sb.id, end_beat_id=eb.id)
    base = {
        "hook": 5,
        "cliffhanger": 5,
        "intensity": 5,
        "clarity": 5,
        "hook_beat_id": sb.id,
        "cliff_beat_id": eb.id,
    }
    base.update(sig)
    return Candidate(id=f"c{ep}_{int(s)}_{int(e)}", kind=kind, segments=[seg], duration_s=e - s,
                     rule_score=score, signals=RuleSignals(**base), content_hash="0" * 64)  # fmt: skip


def llm_scores(c: Candidate, **kw) -> LLMScores:
    d = dict(main_ok=True, hook=8, cliffhanger=8, intensity=7, spoiler_risk=1, clarity_ok=True,
             clarity=7, hook_beat_id=c.signals.hook_beat_id, cliff_beat_id=c.signals.cliff_beat_id,
             why="ok")  # fmt: skip
    d.update(kw)
    return LLMScores(**d)


def test_fusion_uses_llm_with_rule_blend_and_falls_back(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 30.5)
    a = fuse(c, llm_scores(c), rcfg)
    assert a.dims["hook"] == pytest.approx(0.7 * 8 + 0.3 * 5)
    assert a.dims["clarity"] == pytest.approx(0.85 * 7 + 0.15 * 5)
    b = fuse(c, None, rcfg)
    assert b.dims["hook"] == 5 and {"rule_fallback_main", "rule_fallback_clarity"} <= set(b.flags)
    assert b.confidence < a.confidence


def test_spoiler_penalty_and_stitched_factor(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 30.5)
    low = fuse(c, llm_scores(c, spoiler_risk=0), rcfg).final
    high = fuse(c, llm_scores(c, spoiler_risk=10), rcfg).final
    assert low - high == pytest.approx(0.5)
    st = mk(show, 1, 0.0, 30.5, kind="stitched")
    good = fuse(st, llm_scores(st, coherence_ok=True, coherence=10), rcfg)
    bad = fuse(st, llm_scores(st, coherence_ok=True, coherence=0), rcfg)
    unk = fuse(st, llm_scores(st, coherence_ok=False), rcfg)
    assert good.final > unk.final > bad.final and "coherence_unchecked" in unk.flags
    assert good.confidence > bad.confidence and 0.05 <= bad.confidence <= 0.95


def test_confidence_drops_when_llm_and_rules_disagree(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 30.5, hook=2, cliffhanger=2, intensity=2)
    agree = fuse(c, llm_scores(c, hook=2, cliffhanger=2, intensity=2), rcfg).confidence
    clash = fuse(c, llm_scores(c, hook=9, cliffhanger=9, intensity=9), rcfg).confidence
    assert agree > clash


def test_dedupe_overlap_and_diversity_caps(show, rcfg) -> None:
    a = mk(show, 1, 0.0, 30.5)
    b = mk(show, 1, 4.0, 34.5)  # overlaps a heavily -> dropped
    c = mk(show, 1, 60.0, 90.5)
    d = mk(show, 1, 36.0, 66.5)
    fused = [fuse(x, llm_scores(x, hook=9 - i), rcfg) for i, x in enumerate([a, b, c, d])]
    assert candidate_iou(a, b) > rcfg.selection.dedupe_iou
    kept, dropped = select(fused, show, rcfg.selection)
    ids = [f.cand.id for f in kept]
    assert b.id not in ids and dropped["overlap"] == 1
    # same scene (all of ep1 is one scene in the fixture): scene cap = 1
    assert len([i for i in ids if i.startswith("c1_")]) == rcfg.selection.max_per_scene == 1
    assert dropped["scene_cap"] >= 1


def test_episode_and_arc_caps(show, rcfg) -> None:
    sel = rcfg.selection.model_copy(
        update={"max_per_scene": 9, "max_per_episode": 2, "max_per_arc": 3}
    )
    cs = [mk(show, 1, float(s), s + 26.5) for s in (0, 32, 64)] + [mk(show, 2, 0.0, 26.5)]
    fused = [fuse(x, llm_scores(x), rcfg) for x in cs]
    kept, dropped = select(fused, show, sel)
    assert len(kept) == 3 and dropped["episode_cap"] == 1


def test_apply_rerank_blends_and_reorders(show, rcfg) -> None:
    cs = [mk(show, 1, 0.0, 26.5), mk(show, 2, 0.0, 26.5), mk(show, 3, 0.0, 26.5)]
    fused = [fuse(x, llm_scores(x, hook=9 - i), rcfg) for i, x in enumerate(cs)]
    selected, _ = select(fused, show, rcfg.selection)
    first = selected[0].cand.id
    out = apply_rerank(
        selected, {first: 3.0, selected[1].cand.id: 2.0, selected[2].cand.id: 1.0}, rcfg
    )
    assert out[0].cand.id == selected[2].cand.id and out[-1].cand.id == first
    assert all(f.rerank_rank is not None for f in out)


def test_finalize_fills_quotes_from_source_not_llm(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 30.5)
    s = llm_scores(c, why="One sentence.")
    fused = [fuse(c, s, rcfg)]
    promos, st = finalize(show, fused)
    p = promos[0]
    src = {b.id: b for b in show.episode(1).beats}
    assert (
        p.hook.text == src[c.signals.hook_beat_id].text
        and p.cliffhanger.text == src[c.signals.cliff_beat_id].text
    )
    assert p.segments[0].start_ts == "00:00.00" and p.total_duration_s == 30.5
    assert p.rank == 1 and p.post_first_because.startswith("Post first because")
    assert not st.validation_failures


def test_finalize_rejects_hook_outside_first_3s_and_unknown_beats(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 30.5)
    late = next(b for b in show.episode(1).beats if b.start_s > 10)
    bad_hook = fuse(c, llm_scores(c, hook_beat_id=late.id), rcfg)
    bad_cliff = fuse(c, llm_scores(c, cliff_beat_id="E999-S01-B000"), rcfg)
    promos, st = finalize(show, [bad_hook, bad_cliff])
    assert promos == []
    codes = [x for _, codes in st.validation_failures for x in codes]
    assert "hook_not_in_first_3s" in codes and "missing_hook_or_cliff_beat" in codes


def test_trim_applied_only_when_it_still_validates(show, rcfg) -> None:
    beats = show.episode(1).beats
    c = mk(show, 1, 0.0, 42.5)
    start_beat = next(b for b in beats if b.start_s == 4.0)
    end_beat = next(b for b in beats if abs(b.end_s - 30.5) < 1e-6)
    good = llm_scores(c, trim_start_beat_id=start_beat.id, trim_end_beat_id=end_beat.id,
                      hook_beat_id=start_beat.id, cliff_beat_id=end_beat.id)  # fmt: skip
    promos, st = finalize(show, [fuse(c, good, rcfg)])
    assert st.trims_applied == 1 and promos[0].trim_applied
    assert (promos[0].segments[0].start_s, promos[0].segments[0].end_s) == (4.0, 30.5)
    # trimming to <20 s breaks the duration rule -> rejected, original kept
    short_end = next(b for b in beats if abs(b.end_s - 10.5) < 1e-6)
    bad = llm_scores(c, trim_end_beat_id=short_end.id)
    promos, st = finalize(show, [fuse(c, bad, rcfg)])
    assert (
        st.trims_rejected == 1 and not promos[0].trim_applied and promos[0].total_duration_s == 42.5
    )


def test_rank_end_to_end_on_scored_candidates(show, cands, rcfg) -> None:
    from promo_finder.scoring import Scorer, load_scoring_config

    llm = make_llm()
    scores = Scorer(llm, show, load_scoring_config()).score(cands)
    res = rank(show, cands, scores, rcfg, rerank_fn=None)
    assert res.promos and [p.rank for p in res.promos] == list(range(1, len(res.promos) + 1))
    assert not res.final_stats.validation_failures
    for p in res.promos:
        assert validate_segments(show, [_seg(s) for s in p.segments]) == []
        assert 0.05 <= p.confidence <= 0.95 and len(p.why.split()) <= 25
    per_ep = {}
    for p in res.promos:
        per_ep[p.segments[0].episode] = per_ep.get(p.segments[0].episode, 0) + 1
    assert max(per_ep.values()) <= rcfg.selection.max_per_episode


def _seg(s) -> Segment:
    return Segment(
        episode_number=s.episode,
        start_s=s.start_s,
        end_s=s.end_s,
        start_beat_id="x",
        end_beat_id="y",
    )


def test_llm_failure_falls_back_to_rule_scores_and_still_ranks(show, cands, rcfg) -> None:
    res = rank(show, cands, {}, rcfg)  # no LLM scores at all
    assert res.promos
    assert all(
        "rule_fallback_main" in p.flags and p.why.startswith("Rule-based pick") for p in res.promos
    )


def test_promos_with_the_same_cliffhanger_are_deduped(show, rcfg) -> None:
    a = mk(show, 1, 0.0, 26.5)  # ends at 26.5
    b = mk(show, 1, 4.0, 30.5)  # different clip, but...
    c = mk(show, 1, 12.0, 38.5)
    stitched = mk(show, 1, 8.0, 30.5)
    assert not same_ending(a, c, 1.5) and same_ending(b, stitched, 1.5)
    sel = rcfg.selection.model_copy(
        update={"max_per_scene": 9, "max_per_episode": 9, "dedupe_iou": 0.99}
    )
    fused = [fuse(x, llm_scores(x, hook=9 - i), rcfg) for i, x in enumerate([b, stitched, c])]
    kept, dropped = select(fused, show, sel)
    assert dropped["same_ending"] == 1 and len(kept) == 2
