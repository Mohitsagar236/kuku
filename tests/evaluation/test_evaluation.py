from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from promo_finder.domain.models import LLMScores
from promo_finder.evaluation import (
    compare_scores,
    evaluate_golden,
    load_golden,
    promo_iou,
    spearman,
    topk_overlap,
)


def seg(ep: int, s: float, e: float):
    return SimpleNamespace(episode=ep, start_s=s, end_s=e)


def promo(rank: int, *segs):
    return SimpleNamespace(rank=rank, segments=list(segs))


def test_promo_iou_cases() -> None:
    assert promo_iou([seg(1, 0, 10)], [seg(1, 0, 10)]) == 1.0
    assert promo_iou([seg(1, 0, 10)], [seg(2, 0, 10)]) == 0.0  # different episode
    assert promo_iou([seg(1, 0, 10)], [seg(1, 5, 15)]) == pytest.approx(5 / 15)
    two = [seg(1, 0, 10), seg(2, 0, 10)]
    assert promo_iou(two, [seg(1, 0, 10)]) == pytest.approx(0.5)
    assert topk_overlap([[seg(1, 0, 10)], [seg(3, 0, 5)]], [[seg(1, 1, 10)]]) == 1


def test_golden_loads_strings_seconds_and_segments(tmp_path: Path) -> None:
    f = tmp_path / "g.yaml"
    f.write_text(
        "promos:\n"
        "  - {episode: 2, start: '00:10.5', end: 40, note: a}\n"
        "  - segments:\n      - {episode: 3, start: '0:00:05', end: '00:20.0'}\n"
        "      - {episode: 3, start: 50, end: 62}\n    note: b\n"
    )
    g = load_golden(f)
    assert len(g) == 2 and g[0].segments[0].start_s == 10.5 and g[0].segments[0].end_s == 40
    assert len(g[1].segments) == 2 and g[1].note == "b"


def test_golden_malformed_is_a_clear_error(tmp_path: Path) -> None:
    f = tmp_path / "g.yaml"
    f.write_text("promos:\n  - {episode: 1, start: 'oops', end: 5}\n")
    with pytest.raises(ValueError, match="promo #1"):
        load_golden(f)
    f.write_text("promos:\n  - {start: 1, end: 5}\n")
    with pytest.raises(ValueError):
        load_golden(f)
    f.write_text("")
    assert load_golden(f) == []


def test_precision_at_k_and_best_iou(tmp_path: Path) -> None:
    f = tmp_path / "g.yaml"
    f.write_text(
        "promos:\n  - {episode: 1, start: 0, end: 30, note: hit}\n  - {episode: 9, start: 0, end: 30, note: miss}\n"
    )
    g = load_golden(f)
    promos = [promo(1, seg(1, 0, 30)), promo(2, seg(5, 0, 30)), promo(3, seg(1, 20, 50))]
    r = evaluate_golden(g, promos, k=2)
    assert r.precision_at_k == 0.5  # one of the top-2 promos matches a golden promo
    by = {m.note: m for m in r.matches}
    assert by["hit"].best_iou == 1.0 and by["hit"].best_rank == 1 and by["hit"].found_at_k
    assert by["miss"].best_iou == 0.0 and by["miss"].best_rank is None and not by["miss"].found_at_k
    assert r.recall_at_k == 0.5


def test_golden_match_outside_top_k_is_not_counted() -> None:
    g = load_golden_inline([(1, 0, 30)])
    promos = [promo(1, seg(4, 0, 30)), promo(2, seg(4, 40, 70)), promo(3, seg(1, 0, 30))]
    r = evaluate_golden(g, promos, k=2)
    assert r.precision_at_k == 0.0 and r.matches[0].best_rank == 3 and not r.matches[0].found_at_k


def load_golden_inline(rows):
    from promo_finder.evaluation.golden import GoldenPromo, GSeg

    return [GoldenPromo(segments=[GSeg(episode=e, start_s=s, end_s=t)]) for e, s, t in rows]


def test_compare_scores_variance_numbers() -> None:
    a = {
        "x": LLMScores(main_ok=True, hook=5, cliffhanger=5),
        "y": LLMScores(main_ok=True, hook=7, cliffhanger=3),
    }
    b = {
        "x": LLMScores(main_ok=True, hook=6, cliffhanger=5),
        "y": LLMScores(main_ok=True, hook=7, cliffhanger=1),
    }
    d = compare_scores(a, b)
    assert d["hook"].n == 2 and d["hook"].mean_abs_diff == 0.5 and d["hook"].pct_identical == 50.0
    assert d["cliffhanger"].max_abs_diff == 2 and "intensity" not in d  # no data for that dim
    assert compare_scores(a, {}) == {}


def test_spearman() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2], [1, 2]) is None and spearman([1, 1, 1], [1, 2, 3]) is None
