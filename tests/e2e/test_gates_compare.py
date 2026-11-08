from __future__ import annotations

import json
from pathlib import Path

import pytest
from promo_common.llm import LLMSettings
from promo_finder_cli.__main__ import main
from promo_finder_cli.outputs import write_all
from promo_finder_cli.pipeline import RunOptions, run_pipeline

from tests.e2e.helpers import write_show
from tests.shared import SpyMock, make_llm


@pytest.fixture()
def show_dir(tmp_path: Path) -> Path:
    return write_show(tmp_path, n_eps=8)


def run(show_dir: Path, tmp_path: Path, **kw):
    llm = kw.pop("llm", None) or make_llm()
    opts = RunOptions(out_root=tmp_path / "out", persist_cache=False, **kw)
    return run_pipeline(show_dir, opts, LLMSettings(_env_file=None), llm=llm)


def test_report_shows_gates_refinement_and_the_score_formula(
    show_dir: Path, tmp_path: Path
) -> None:
    res = run(show_dir, tmp_path, ablation=False, stability=False)
    out = write_all(res)
    rep = (out / "quality_report.md").read_text()
    assert (
        "## 2b. Hard gates and end-point refinement" in rep
        and "hook < 5" in rep
        and "cliffhanger < 5" in rep
    )
    assert "End refinement:" in rep
    assert "## 2c. Final-score formula and rule vs LLM share" in rep
    assert "0.3*hook + 0.3*cliffhanger + 0.2*clarity + 0.2*intensity" in rep
    assert "the LLM contributes 73%" in rep  # 0.3+0.3+0.2 at 70% and 0.2 at 85%... by construction
    meta = json.loads((out / "run_metadata.json").read_text())
    assert set(meta["gates"]) == {"dropped", "gated", "hook_first_pass"}
    assert set(meta["end_refinement"]) == {"refined", "rejected"}
    assert "by_kind" in meta["retries"] and "mean" in meta["latency_ms"]


def test_every_retry_error_kind_lands_in_the_run_metadata(show_dir: Path, tmp_path: Path) -> None:
    llm = make_llm(SpyMock(faults=["transient", "rate_limit"]))
    res = run(show_dir, tmp_path, llm=llm, ablation=False, stability=False)
    meta = json.loads((write_all(res) / "run_metadata.json").read_text())
    assert meta["retries"]["by_kind"] == {"http_5xx:503": 1, "429_rate_limit": 1}
    assert [e["kind"] for e in meta["retries"]["events"]] == ["http_5xx:503", "429_rate_limit"]
    assert all(e["phase"] == "main" for e in meta["retries"]["events"])


def test_gated_candidates_do_not_reach_the_delivered_list(show_dir: Path, tmp_path: Path) -> None:
    class Weak(SpyMock):
        """Every ending looks resolved to this 'model'."""

        def _score(self, meta, noise, fault):
            out = super()._score(meta, noise, fault)
            for r in out["results"]:
                r["h"], r["c"] = 8, 3
            return out

        def _clarity(self, meta, noise, fault):
            out = super()._clarity(meta, noise, fault)
            for r in out["results"]:
                r["cliff"] = 3 if "cliff" in r else None
            return out

    res = run(show_dir, tmp_path, llm=make_llm(Weak()), ablation=False, stability=False)
    delivered = [p for p in res.main.result.promos if p.kind == "single"]
    assert delivered == []  # nothing with a cliffhanger score of 3 may be delivered
    assert res.main.result.gated.get("cliffhanger", 0) > 0


def test_smoke_writes_a_summary_and_compare_prints_two_models_side_by_side(
    show_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for model in ("mock-a", "mock-b"):
        rc = main(["smoke", str(show_dir), "--episodes", "1-3", "--provider", "mock", "--model", model,
                   "--no-cache", "--out", str(tmp_path / "o")])  # fmt: skip
        assert rc == 0
    runs = sorted((tmp_path / "o").glob("*/smoke-*"))
    assert len(runs) == 2
    for r in runs:
        s = json.loads((r / "smoke_summary.json").read_text())
        assert (
            s["calls"] > 0
            and "latency_ms" in s
            and len(s["top3"]) >= 1
            and "transient_retries" in s
        )
    capsys.readouterr()
    assert main(["compare", str(runs[0]), str(runs[1])]) == 0
    out = capsys.readouterr().out
    assert (
        "| model | mock/mock-a | mock/mock-b |" in out.replace("  ", " ")
        or "mock-a" in out
        and "mock-b" in out
    )
    for needle in (
        "latency per call",
        "candidate answers rejected",
        "truncated responses",
        "Top 3: A",
        "Top 3: B",
        "- hook",
        "- cliffhanger",
    ):
        assert needle in out, needle
    assert list((tmp_path / "o").glob("*/model_comparison-*.md"))
    assert main(["compare", str(tmp_path), str(runs[1])]) == 2  # missing summary -> clean error


def test_unknown_provider_is_a_clean_error_not_a_traceback(
    show_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["run", str(show_dir), "--provider", "bogus", "--out", str(tmp_path / "o")])
    assert rc == 2
    assert "unknown LLM provider 'bogus'" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()  # failed before writing anything


def test_runs_in_the_same_second_never_share_a_folder(tmp_path: Path) -> None:
    from promo_finder_cli.pipeline import _reserve_dir

    dirs = [_reserve_dir(tmp_path, "20260101-000000") for _ in range(3)]
    assert [d.name for d in dirs] == ["20260101-000000", "20260101-000000-2", "20260101-000000-3"]
