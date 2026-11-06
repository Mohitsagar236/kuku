from candidate_service.context import EpCtx
from candidate_service.signals import make_view, score_cliff, score_hook
from candidate_service.windows import enumerate_windows, nms
from promo_common.cuts import CONTENT_TYPES

from tests.candidate.conftest import A, C, D, S, dense_episode, make_episode


def test_windows_never_cut_inside_dialogue_or_action(cfg, lex) -> None:
    # a long overlapping action straddles what would otherwise be a nice boundary
    specs = [(D, i * 4.0, i * 4.0 + 2.5, f"line {i} is here today") for i in range(30)]
    specs.append((A, 38.0, 47.0, "a very long action that spans many lines"))
    ctx = EpCtx(make_episode(1, specs), cfg)
    wins = enumerate_windows(ctx, lex, cfg)
    assert wins
    for w in wins:
        _, s, e = w.segs[0]
        for b in ctx.beats:
            if b.type in (D, A):
                assert not (b.start_s + 1e-6 < s < b.end_s - 1e-6), (s, b.id)
                assert not (b.start_s + 1e-6 < e < b.end_s - 1e-6), (e, b.id)
        # end >= end of every dialogue/action beat that started inside the window
        for b in ctx.beats_in(s, e):
            if b.type in (D, A):
                assert b.end_s <= e + 1e-6


def test_window_may_start_on_camera_beat_when_hook_follows_within_3s(cfg, lex) -> None:
    specs = [(C, 0.0, 6.0, "wide shot of the harbor")]
    specs += [(D, 1.5 + i * 4, 4.0 + i * 4, f"spoken line {i} right here") for i in range(15)]
    ctx = EpCtx(make_episode(1, specs), cfg)
    assert 0.0 in ctx.valid_starts  # camera boundary is a legal cut point
    w0 = [w for w in enumerate_windows(ctx, lex, cfg) if w.segs[0][1] == 0.0]
    assert w0 and all(
        ctx.beats[next(i for i, b in enumerate(ctx.beats) if b.id == w.signals.hook_beat_id)].type
        in CONTENT_TYPES
        for w in w0
    )


def test_no_window_when_first_content_beat_is_later_than_3s(cfg) -> None:
    specs = [(C, 0.0, 10.0, "wide shot"), (D, 4.0, 6.0, "late line comes now")]
    specs += [(D, 10 + i * 4, 12.5 + i * 4, f"another line {i} appears") for i in range(12)]
    ctx = EpCtx(make_episode(1, specs), cfg)
    assert 0.0 not in ctx.valid_starts  # first content beat at 4.0 > 0 + 3.0


def test_camera_beats_are_never_hook_or_cliff_content(cfg, lex) -> None:
    specs = [(C, 0, 2, "Close-up: she slaps him and screams, cut to black")]
    specs += [(D, 1 + i * 4, 3.5 + i * 4, f"calm line {i} is spoken") for i in range(15)]
    ctx = EpCtx(make_episode(1, specs), cfg)
    hook = score_hook(ctx, 0.0, lex, cfg)
    assert hook.beat_id is not None and "B000" not in hook.beat_id  # camera is B000
    assert "slaps" not in hook.terms and "slap" not in hook.terms
    for w in enumerate_windows(ctx, lex, cfg):
        for bid in (w.signals.hook_beat_id, w.signals.cliff_beat_id):
            assert next(b for b in ctx.beats if b.id == bid).type is not C


def test_hook_prefers_slap_and_shouting_over_quiet_line(cfg, lex) -> None:
    loud = make_episode(
        1,
        [
            (A, 0.5, 2.0, "He slaps her across the face."),
            (D, 2.0, 3.0, "You lied to me!", "BEN", ["furious"]),
        ]
        + [(D, 5 + i * 4, 7 + i * 4, f"filler line {i} ok") for i in range(10)],
    )
    quiet = make_episode(
        1,
        [(D, 2.5, 4.0, "Pass the salt please")]
        + [(D, 5 + i * 4, 7 + i * 4, f"filler line {i} ok") for i in range(10)],
    )
    assert (
        score_hook(EpCtx(loud, cfg), 0.5, lex, cfg).score
        > score_hook(EpCtx(quiet, cfg), 2.5, lex, cfg).score + 3
    )


def test_cliffhanger_episode_end_prior_question_and_cues(cfg, lex) -> None:
    base = [(D, i * 4.0, i * 4.0 + 2.5, f"statement number {i} is calm") for i in range(20)]
    ep_end_q = make_episode(1, base + [(D, 80, 82, "Who are you?")])
    ctx = EpCtx(ep_end_q, cfg)
    at_end = score_cliff(make_view(ctx, 50.0, 82.0), lex, cfg)
    mid = score_cliff(make_view(ctx, 20.0, 52.5), lex, cfg)
    assert at_end.at_end and at_end.score > mid.score + 3
    # sound-fx sting + trailing camera "cut to black" add cues
    ep_cue = make_episode(
        1,
        base
        + [
            (D, 80, 82, "It is done."),
            (S, 82, 83, "Ominous sting."),
            (C, 82, 85, "Abrupt cut to black."),
        ],
    )
    cue = score_cliff(make_view(EpCtx(ep_cue, cfg), 52.0, 83.0), lex, cfg)
    assert {"sting", "ominous"} <= set(cue.cues) or "sting" in cue.cues
    # resolution phrase at the end is penalised
    ep_res = make_episode(1, base + [(D, 80, 82, "Thank you, goodbye.")])
    res = score_cliff(make_view(EpCtx(ep_res, cfg), 50.0, 82.0), lex, cfg)
    assert res.score < at_end.score


def test_nms_removes_overlaps_and_shared_edges(cfg, lex) -> None:
    ctx = EpCtx(dense_episode(1, 30), cfg)
    wins = enumerate_windows(ctx, lex, cfg)
    kept = nms(wins, cfg, 10)
    assert 0 < len(kept) <= 10 and len(kept) < len(wins)
    for i, a in enumerate(kept):
        for b in kept[i + 1 :]:
            assert abs(a.segs[0][1] - b.segs[0][1]) >= cfg.nms_edge_tol_s
            assert abs(a.segs[0][2] - b.segs[0][2]) >= cfg.nms_edge_tol_s
