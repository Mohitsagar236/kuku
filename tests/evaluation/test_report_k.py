"""Report denominators follow the real number of promos (regression: "3/10" on a 3-promo run),
and verdicts come from overlap/k, never from a hardcoded 10."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from promo_finder.application.models import RunOptions
from promo_finder.application.pipeline import run_pipeline
from promo_finder.evaluation.feedback import evaluate_feedback
from promo_finder.evaluation.report import dependence_verdict, eff_k, signal_occurrences
from promo_finder.infrastructure.artifacts import write_all
from promo_finder.infrastructure.llm import LLMSettings
from tests.e2e.helpers import write_show
from tests.shared import make_llm


@pytest.mark.parametrize(
    ("overlap", "k", "word"),
    [(3, 3, "ROBUST"), (2, 3, "MODERATE"), (1, 3, "STRONG"), (10, 10, "ROBUST"),
     (8, 10, "ROBUST"), (5, 10, "MODERATE"), (4, 10, "STRONG"), (0, 0, "n/a")],
)  # fmt: skip
def test_verdict_uses_the_overlap_fraction(overlap: int, k: int, word: str) -> None:
    assert dependence_verdict(overlap, k).startswith(word)


def test_identical_rankings_are_never_called_strong_dependence() -> None:
    for n in range(1, 11):
        assert "STRONG" not in dependence_verdict(n, n)


def test_effective_k_is_min_of_ten_and_the_shortest_list() -> None:
    assert eff_k([1, 2, 3]) == 3 and eff_k(list(range(40))) == 10
    assert eff_k([1, 2, 3], list(range(40))) == 3 and eff_k([]) == 0


def test_signal_occurrences_counts_only_real_mentions(show) -> None:
    assert signal_occurrences(show, ["zzz-never-appears"]) == 0
    assert signal_occurrences(show, []) == 0
    assert signal_occurrences(show, ["you lied to me"]) > 0  # the synthetic scripts contain it


def _report(tmp_path: Path, n_eps: int) -> tuple[str, int]:
    show_dir = write_show(tmp_path, n_eps=n_eps)
    res = run_pipeline(
        show_dir,
        RunOptions(out_root=tmp_path / "out", persist_cache=False, stability=False),
        LLMSettings(_env_file=None),
        llm=make_llm(),
    )
    return (write_all(res) / "quality_report.md").read_text(), len(res.main.result.promos)


def test_a_small_run_never_reports_ten_as_the_denominator(tmp_path: Path) -> None:
    rep, n = _report(tmp_path, 2)
    assert 0 < n < 10
    assert not re.search(r"\b\d+/10\b", rep), re.findall(r"[^\n]*\b\d+/10\b[^\n]*", rep)
    assert "Top-10" not in rep and "top 10" not in rep.replace("top 10%", "")
    assert f"Top-{n} overlap" in rep and f"/{n}** (IoU" in rep
    assert "STRONG dependence" not in rep  # identical rankings, however few


def test_the_literal_cue_verdict_is_not_applicable_when_the_signal_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import promo_finder.evaluation.report as report

    monkeypatch.setattr(report, "signal_occurrences", lambda show, terms: 0)
    rep, _ = _report(tmp_path, 3)
    assert "not applicable: signal absent in this show" in rep
    assert "ROBUST" not in rep.split("literal_cliffhanger")[1].split("###")[0]


def test_precision_tile_uses_the_real_number_of_promos() -> None:
    promos = [
        {"rank": i, "id": f"p{i}", "segments": [{"episode": 1, "start_s": 0, "end_s": 30}],
         "final_score": 5.0}
        for i in range(1, 4)
    ]  # fmt: skip
    s = evaluate_feedback([], promos, k=10)
    assert s.k == 3 and s.top == 3  # "Precision @ 3", not "@ 10"
