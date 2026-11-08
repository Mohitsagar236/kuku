"""Cuts must not open mid-shot: a CAMERA beat just before the first line belongs to the cut."""

from __future__ import annotations

from promo_finder.domain.models import Segment
from promo_finder.domain.validation import validate_segments
from promo_finder.ranking.build import _snap_starts
from tests.candidate.conftest import C, D, make_episode
from tests.conftest import make_show


def _show(camera_gap: float):
    # camera shot at 10.0, the line it frames at 10.0 + camera_gap, then filler to 60 s
    specs = [(D, 0.0, 2.0, "intro line one two three")]
    specs += [(C, 10.0, 10.0 + camera_gap, "Close-up on the speaker")]
    specs += [(D, 10.0 + camera_gap, 12.0 + camera_gap, "the framed line here")]
    specs += [(D, 12.0 + i * 3.0, 14.0 + i * 3.0, f"filler line {i} text") for i in range(1, 19)]
    specs += [(D, 66.0, 70.5, "the last line here")]
    return make_show(make_episode(1, specs))


def _seg(show, start: float, end: float) -> Segment:
    return Segment(episode_number=1, start_s=start, end_s=end, start_beat_id="", end_beat_id="")


def test_start_moves_back_onto_the_introducing_camera_shot():
    show = _show(0.4)
    out, n = _snap_starts(show, [_seg(show, 10.4, 44.0)])
    assert n == 1 and out[0].start_s == 10.0


def test_a_distant_camera_beat_is_not_snapped():
    show = _show(1.5)  # more than 0.6 s: a separate shot, leave the cut alone
    out, n = _snap_starts(show, [_seg(show, 11.5, 44.0)])
    assert n == 0 and out[0].start_s == 11.5


def test_snap_is_refused_when_it_would_break_the_length_limit():
    show = _show(0.5)
    seg = _seg(show, 10.5, 70.5)  # exactly 60 s: moving back 0.5 s would make it 60.5 s
    assert not validate_segments(show, [seg])  # the original cut is valid
    out, n = _snap_starts(show, [seg])
    assert n == 0 and out[0].start_s == 10.5
