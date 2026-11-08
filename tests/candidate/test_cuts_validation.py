from promo_finder.domain.cuts import is_valid_cut, protected_spans
from promo_finder.domain.models import Segment
from promo_finder.domain.validation import validate_segments
from tests.candidate.conftest import A, C, D, S, dense_episode, make_episode, make_show


def test_cut_inside_dialogue_or_action_is_invalid_but_camera_is_fine() -> None:
    ep = make_episode(1, [(D, 1, 5, "hello there friend"), (A, 6, 9, "walks"), (C, 2, 8, "wide")])
    spans = protected_spans(ep.beats)
    assert spans == [(1, 5), (6, 9)]
    assert not is_valid_cut(3.0, spans) and not is_valid_cut(7.0, spans)
    assert is_valid_cut(5.0, spans) and is_valid_cut(1.0, spans)  # boundaries
    assert is_valid_cut(5.5, spans)  # between beats
    assert is_valid_cut(2.0, spans) is False  # inside dialogue even though a camera starts there


def test_valid_promo_passes() -> None:
    show = make_show(dense_episode(1, 20))
    seg = Segment(episode_number=1, start_s=0, end_s=30.5, start_beat_id="a", end_beat_id="b")
    assert validate_segments(show, [seg]) == []


def test_each_violation_is_reported() -> None:
    show = make_show(dense_episode(1, 20), dense_episode(2, 20))

    def seg(ep: int, s: float, e: float) -> Segment:
        return Segment(episode_number=ep, start_s=s, end_s=e, start_beat_id="x", end_beat_id="y")

    assert validate_segments(show, []) == ["no_segments"]
    assert "duration_out_of_range" in validate_segments(show, [seg(1, 0, 10.5)])
    assert "duration_out_of_range" in validate_segments(show, [seg(1, 0, 70.5)])
    assert "seg0:unknown_episode" in validate_segments(show, [seg(9, 0, 30)])
    assert "seg0:start_not_on_beat" in validate_segments(show, [seg(1, 0.37, 30.5)])
    assert "seg0:end_not_on_beat" in validate_segments(show, [seg(1, 0, 30.4)])
    assert "seg0:cut_inside_beat" in validate_segments(
        show, [seg(1, 0, 29.0)]
    )  # 29.0 is mid-line (28.0-30.5)
    # 0..2.5 is a dialogue beat; ending at 1.0 would be mid-dialogue (and not on a beat end)
    errs = validate_segments(show, [seg(1, 4, 5.0), seg(1, 8, 30.5)])
    assert "seg0:cut_inside_beat" in errs and "seg0:segment_too_short" in errs
    assert "seg1:out_of_order" in validate_segments(show, [seg(2, 0, 14.5), seg(1, 0, 14.5)])
    assert "seg1:out_of_order" in validate_segments(show, [seg(1, 0, 14.5), seg(1, 8, 22.5)])
    four = [seg(1, 0, 6.5), seg(1, 8, 14.5), seg(1, 16, 22.5), seg(1, 24, 30.5)]
    assert "too_many_segments" in validate_segments(show, four)


def test_hook_must_fall_in_first_three_seconds() -> None:
    ep = make_episode(
        1,
        [
            (C, 0, 10, "wide shot"),
            (D, 4.0, 6.0, "late line comes here"),
            (D, 10, 40, "long one now"),
        ],
    )
    show = make_show(ep)
    s = Segment(episode_number=1, start_s=0, end_s=40, start_beat_id="a", end_beat_id="b")
    assert "no_hook_in_first_window" in validate_segments(show, [s])
    ep2 = make_episode(1, [(C, 0, 10, "wide shot"), (S, 2.5, 3.0, "sting"), (D, 10, 40, "x y z")])
    assert "no_hook_in_first_window" not in validate_segments(make_show(ep2), [s])
