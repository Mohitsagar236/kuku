from __future__ import annotations

import pytest

from promo_finder.domain.models import LLMScores
from promo_finder.ranking import load_ranking_config, rank
from promo_finder.ranking.fusion import attribution, failed_gates, fuse
from tests.ranking.test_ranking import llm_scores, mk
from tests.shared import make_llm  # noqa: F401  (fixtures: show)


@pytest.fixture()
def rcfg():
    return load_ranking_config()


def test_default_gates_are_5_and_5(rcfg) -> None:
    assert rcfg.gates.min_hook == 5 and rcfg.gates.min_cliffhanger == 5


@pytest.mark.parametrize(
    ("hook", "cliff", "expected"),
    [
        (5, 5, []),
        (8, 9, []),
        (4, 8, ["hook"]),
        (8, 4, ["cliffhanger"]),
        (3, 2, ["hook", "cliffhanger"]),
    ],
)
def test_gate_thresholds_are_inclusive_at_five(rcfg, hook, cliff, expected) -> None:
    assert failed_gates(LLMScores(main_ok=True, hook=hook, cliffhanger=cliff), rcfg) == expected


def test_candidates_without_llm_scores_are_not_gated(rcfg) -> None:
    assert failed_gates(None, rcfg) == [] and failed_gates(LLMScores(main_ok=False), rcfg) == []


def test_gate_uses_the_delivered_ends_cliffhanger(rcfg) -> None:
    moved = LLMScores(main_ok=True, hook=8, cliffhanger=3, end_changed=True, end_cliff=8)
    assert failed_gates(moved, rcfg) == []  # the weak ORIGINAL end was replaced by a strong one
    unmoved = LLMScores(main_ok=True, hook=8, cliffhanger=3, end_changed=False, end_cliff=8)
    assert failed_gates(unmoved, rcfg) == ["cliffhanger"]


def test_gated_candidates_are_never_delivered_and_are_counted(show, rcfg) -> None:
    # smoke #3 in miniature: a resolved ending (cliff 4) must not be delivered however good the rest
    ok = mk(show, 1, 0.0, 26.5)
    resolved = mk(show, 2, 0.0, 26.5)
    weak_hook = mk(show, 3, 0.0, 26.5)
    both = mk(show, 1, 36.0, 66.5)
    scores = {
        ok.id: llm_scores(ok),
        resolved.id: llm_scores(resolved, cliffhanger=4, hook=10, intensity=10, clarity=10),
        weak_hook.id: llm_scores(weak_hook, hook=4, cliffhanger=9),
        both.id: llm_scores(both, hook=2, cliffhanger=2),
    }
    res = rank(show, [ok, resolved, weak_hook, both], scores, rcfg)
    assert [p.id for p in res.promos] == [ok.id]
    assert res.gated == {"cliffhanger": 1, "hook": 1, "both": 1}
    assert (
        res.dropped["gate_cliffhanger"] == 1
        and res.dropped["gate_hook"] == 1
        and res.dropped["gate_both"] == 1
    )
    assert res.n_ranked == 4


def test_thresholds_are_configurable(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 26.5)
    s = {c.id: llm_scores(c, cliffhanger=4)}
    assert rank(show, [c], s, rcfg).promos == []
    loose = rcfg.model_copy(update={"gates": rcfg.gates.model_copy(update={"min_cliffhanger": 3})})
    assert len(rank(show, [c], s, loose).promos) == 1


def test_attribution_matches_the_configured_shares(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 26.5, hook=6, cliffhanger=6, intensity=6, clarity=6)
    f = fuse(c, llm_scores(c, hook=6, cliffhanger=6, intensity=6, clarity=6, spoiler_risk=0), rcfg)
    rule, llm = attribution(f, rcfg)
    assert rule + llm == pytest.approx(f.final)  # nothing is lost or double counted
    assert rule / (rule + llm) == pytest.approx(0.27)  # 0.3*(0.3+0.3+0.2)... = 27% rule, 73% LLM
    spoiler = fuse(
        c, llm_scores(c, hook=6, cliffhanger=6, intensity=6, clarity=6, spoiler_risk=10), rcfg
    )
    assert attribution(spoiler, rcfg)[1] < llm  # the spoiler penalty is an LLM-side term


def test_fallback_candidates_are_100_percent_rules(show, rcfg) -> None:
    c = mk(show, 1, 0.0, 26.5)
    rule, llm = attribution(fuse(c, None, rcfg), rcfg)
    assert llm == 0 and rule > 0


def _refined_collision(show, rcfg):
    """A and B pass selection (IoU 0.12, different ends); A's refined end then equals B's end."""
    rcfg.selection.max_per_scene = 5  # both start in the same scene of the fixture
    a, b = mk(show, 1, 0.0, 26.5), mk(show, 1, 20.0, 54.5)
    end_b = next(x for x in show.episode(1).beats if abs(x.end_s - 54.5) < 1e-6)
    scores = {
        a.id: llm_scores(a, hook=9, cliffhanger=9, intensity=9, clarity=9,
                         end_changed=True, end_beat_id=end_b.id, end_cliff=8),
        b.id: llm_scores(b, hook=7, cliffhanger=7, intensity=7, clarity=7),
    }  # fmt: skip
    return [a, b], scores


def test_refinement_that_lands_on_another_promos_ending_does_not_deliver_both(show, rcfg) -> None:
    cands, scores = _refined_collision(show, rcfg)
    res = rank(show, cands, scores, rcfg)
    assert len(res.selected) == 2  # selection saw two distinct candidates...
    assert [p.id for p in res.promos] == [cands[0].id]  # ...but only the better cut is delivered
    assert res.promos[0].end_refined and res.promos[0].segments[0].end_s == 54.5
    assert res.dropped["redundant_after_refinement"] == 1
    assert res.final_stats.dropped_redundant_after_refinement == 1
    assert res.promos[0].rank == 1


def test_delivered_promos_never_overlap_or_share_an_ending(show, rcfg) -> None:
    cands, scores = _refined_collision(show, rcfg)
    ps = rank(show, cands, scores, rcfg).promos
    for i, p in enumerate(ps):
        for q in ps[i + 1 :]:
            assert p.segments[-1].end_s != q.segments[-1].end_s
