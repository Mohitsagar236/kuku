"""A nudge may never cut out the promo's hook or cliffhanger (regression: demo promo #3)."""

from __future__ import annotations

from promo_finder.evaluation.feedback import FSeg, PromoView, anchors_for, nudge
from tests.conftest import *  # noqa: F403  (the `show` fixture)

# synthetic episode 1: line i runs [4i, 4i+2.5]; the cut 4.0-30.5 hooks on line 1, ends on line 7
SEG = [FSeg(episode=1, start_s=4.0, end_s=30.5)]


def _view(show) -> PromoView:
    beats = show.episode(1).beats
    hook = next(b for b in beats if b.start_s == 4.0)
    cliff = next(b for b in beats if b.end_s == 30.5)
    return PromoView(1, "p1", tuple(SEG), None, hook.id, cliff.id)


def test_without_anchors_the_old_behaviour_would_have_allowed_it(show) -> None:
    assert nudge(show, SEG, 0, "start", to=8.0).ok  # the bug: hook at 4.0 is now outside the clip


def test_a_start_after_the_hook_is_refused_and_never_suggested(show) -> None:
    a = anchors_for(show, _view(show))
    r = nudge(show, SEG, 0, "start", to=8.0, anchors=a)
    assert not r.ok and r.segments == SEG
    assert any("cutting it out" in x and "hook" in x for x in r.reasons)
    assert all(t <= 4.0 for t in r.suggestions)  # no "nearest allowed" cut past the hook
    for delta in (0.5, 4.0, 8.0):
        assert not nudge(show, SEG, 0, "start", delta=delta, anchors=a).ok


def test_an_end_before_the_cliffhanger_is_refused_and_never_suggested(show) -> None:
    a = anchors_for(show, _view(show))
    r = nudge(show, SEG, 0, "end", to=26.5, anchors=a)  # still >= 20 s long, still on a beat
    assert not r.ok and any("cliffhanger" in x for x in r.reasons)
    assert all(t >= 30.5 for t in r.suggestions)  # every suggestion keeps the cliffhanger
    for t in r.suggestions:
        assert nudge(show, SEG, 0, "end", to=t, anchors=a).ok


def test_moves_that_keep_hook_and_cliffhanger_still_work(show) -> None:
    a = anchors_for(show, _view(show))
    assert nudge(show, SEG, 0, "end", delta=4.0, anchors=a).ok  # extend past the cliffhanger
    assert nudge(show, SEG, 0, "start", to=0.0, anchors=a).ok is False  # hook would be 4 s in
    assert nudge(show, SEG, 0, "start", to=4.0, anchors=a).ok  # same start is fine


def test_a_start_that_pushes_the_hook_past_three_seconds_is_refused(show) -> None:
    seg = [FSeg(episode=1, start_s=4.0, end_s=40.5)]
    r = nudge(show, seg, 0, "start", delta=-4.0, anchors=anchors_for(show, _view(show)))
    assert not r.ok and any("first 3 s" in x for x in r.reasons)


def test_unknown_beats_or_other_episodes_do_not_block_nudging(show) -> None:
    assert anchors_for(show, PromoView(1, "p", tuple(SEG), None, "nope", "nope")) is None
    assert anchors_for(show, None) is None
