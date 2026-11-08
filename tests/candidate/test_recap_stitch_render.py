import hashlib

from promo_finder.candidates import generate_candidates
from promo_finder.candidates.context import EpCtx
from promo_finder.candidates.generate import _fingerprint, _replayed, build_contexts
from promo_finder.candidates.recap import detect_recap, normalize_line
from promo_finder.candidates.render import estimate_tokens, project, render_candidate
from promo_finder.domain.models import Candidate
from promo_finder.domain.validation import validate_segments
from tests.candidate.conftest import D, dense_episode, line_text, make_episode, make_show


def _replay_show():
    """Episode 2 opens by replaying the last lines of episode 1 (slightly re-punctuated)."""
    ep1 = dense_episode(1, 24, words="harbor")
    tail = [b for b in ep1.beats if b.start_s >= 60]  # last ~36s of ep1
    specs = [
        (D, i * 4.0 + 0.5, i * 4.0 + 3.0, b.text.capitalize() + "!", b.speaker)
        for i, b in enumerate(tail[:6])
    ]
    specs += [
        (D, 30 + i * 4.0, 32.5 + i * 4.0, line_text(i, "fresh", kstride=17), "BEN")
        for i in range(16)
    ]
    return make_show(ep1, make_episode(2, specs, notes="Picks up directly from Episode 1."))


def test_detect_recap_is_fuzzy_and_dialogue_only(cfg) -> None:
    show = _replay_show()
    rec = detect_recap(show.episodes[0], show.episodes[1], cfg)
    assert len(rec) == 6
    assert detect_recap(None, show.episodes[1], cfg) == {}
    other = make_show(dense_episode(1, 24, words="harbor"), dense_episode(2, 24, words="castle"))
    assert detect_recap(other.episodes[0], other.episodes[1], cfg) == {}


def test_recap_duplicate_is_deduped_across_episode_boundary(cfg, lex) -> None:
    show = _replay_show()
    cands, stats = generate_candidates(show, cfg, lex)
    assert stats.recap_duplicates_removed >= 1
    ctxs = build_contexts(show, cfg)
    # no two surviving candidates from different episodes replay >=2 lines of each other
    fps = []
    for c in cands:
        from promo_finder.candidates.windows import Built

        fps.append(
            (
                c,
                _fingerprint(
                    Built(
                        c.kind,
                        [(s.episode_number, s.start_s, s.end_s) for s in c.segments],
                        c.signals,
                        c.rule_score,
                    ),
                    ctxs,
                    cfg.recap_min_words,
                ),
            )
        )
    for i, (a, fa) in enumerate(fps):
        for b, fb in fps[i + 1 :]:
            if a.episodes != b.episodes and fa and fb:
                shared = _replayed(fa, fb)
                assert not (shared >= 2 and shared / min(len(fa), len(fb)) >= cfg.recap_overlap)
    assert any(c.duplicate_of for c in cands)


def test_same_source_beats_are_not_recap_duplicates(cfg, lex) -> None:
    """Regression: a stitched candidate reusing an episode's beats must not delete its single."""
    show = make_show(dense_episode(1, 30, words="north"), dense_episode(2, 30, words="south"))
    _, stats = generate_candidates(show, cfg, lex)
    assert stats.recap_duplicates_removed == 0


def test_normalize_line() -> None:
    assert normalize_line("  “Who ARE you?!”  ") == "who are you"


def test_stitched_candidates_respect_all_constraints(cfg, lex) -> None:
    eps = []
    for n in (1, 2, 3):
        specs = [
            (
                D,
                i * 4.0,
                i * 4.0 + 2.5,
                f"line {i} ep{n} you lied to me!"
                if i % 3 == 0
                else f"quiet line {i} ep{n} goes on",
                "ANA" if i % 2 else "BEN",
            )
            for i in range(30)
        ]
        eps.append(make_episode(n, specs, notes="Picks up directly from the previous episode."))
    show = make_show(*eps)
    cands, _ = generate_candidates(show, cfg, lex)
    stitched = [c for c in cands if c.kind == "stitched"]
    assert stitched
    for c in stitched:
        assert 2 <= len(c.segments) <= 3
        assert all(s.duration_s >= cfg.min_segment_s for s in c.segments)
        assert cfg.min_total_s <= c.duration_s <= cfg.max_total_s
        assert abs(c.duration_s - sum(s.duration_s for s in c.segments)) < 1e-6
        keys = [(s.episode_number, s.start_s) for s in c.segments]
        assert keys == sorted(keys)
        assert validate_segments(show, c.segments) == []
        assert "stitched" in " ".join(c.signals.risks)


def test_render_excludes_camera_and_cost_projection(cfg, lex) -> None:
    show = make_show(dense_episode(1, 30), dense_episode(2, 30, words="beta"))
    cands, _ = generate_candidates(show, cfg, lex)
    text = render_candidate(cands[0], show)
    assert "[camera]" not in text and "[E001" in text or "[E002" in text
    pc, pe = project(show, cands)
    assert pc.calls == len(cands) and pe.calls < pc.calls
    assert pe.tokens_in < pc.tokens_in  # batching shares beats and the prompt
    assert pc.cost(1.0, 5.0) > 0
    assert estimate_tokens("abcd" * 10) == 10


def test_content_hash_tracks_content(cfg, lex) -> None:
    a = make_show(dense_episode(1, 30))
    b = make_show(dense_episode(1, 30))
    ca, _ = generate_candidates(a, cfg, lex)
    cb, _ = generate_candidates(b, cfg, lex)
    assert [c.content_hash for c in ca] == [c.content_hash for c in cb]  # deterministic
    b.episodes[0].scenes[0].beats[5].text = "something entirely different now"
    cc, _ = generate_candidates(b, cfg, lex)
    assert {c.content_hash for c in ca} != {c.content_hash for c in cc}
    assert isinstance(ca[0], Candidate) and len(ca[0].content_hash) == 64
    hashlib.sha256()  # silence unused import linters


def test_ctx_scene_start_and_boundary_ids(cfg) -> None:
    ctx = EpCtx(dense_episode(1, 20), cfg)
    assert ctx.scene_start(10.0) == 0.0
    sid, eid = ctx.boundary_ids(0.0, 30.5)
    assert sid.endswith("B000") and eid.startswith("E001")
