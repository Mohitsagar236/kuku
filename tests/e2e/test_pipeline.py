from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from ingestion_service import load_show
from promo_common.llm import LLMSettings
from promo_common.llm.cache import MemoryCache
from promo_common.schemas import Promo, Segment
from promo_common.validation import validate_segments
from promo_finder_cli.__main__ import main
from promo_finder_cli.outputs import write_all
from promo_finder_cli.pipeline import RunInterrupted, RunOptions, run_pipeline

from tests.e2e.helpers import write_show
from tests.shared import SpyMock, make_llm


@pytest.fixture()
def show_dir(tmp_path: Path) -> Path:
    return write_show(tmp_path)


def run(show_dir: Path, tmp_path: Path, **kw):
    llm = kw.pop("llm", None) or make_llm()
    opts = RunOptions(out_root=tmp_path / "out", persist_cache=False, **kw)
    return run_pipeline(show_dir, opts, LLMSettings(_env_file=None), llm=llm)


def test_full_run_writes_every_deliverable(show_dir: Path, tmp_path: Path) -> None:
    res = run(show_dir, tmp_path, stability=True)
    out = write_all(res)
    for name in (
        "promos.json",
        "promos.csv",
        "ranking.md",
        "quality_report.md",
        "run_metadata.json",
    ):
        assert (out / name).stat().st_size > 100, name
    assert list((out / "llm_raw").glob("*.json")), "raw LLM responses must be kept for audit"

    doc = json.loads((out / "promos.json").read_text())
    promos = [Promo(**p) for p in doc["promos"]]
    assert promos and [p.rank for p in promos] == list(range(1, len(promos) + 1))

    show = load_show(show_dir)
    beat_texts = {b.text for e in show.episodes for b in e.beats}
    for p in promos:
        segs = [
            Segment(
                episode_number=s.episode,
                start_s=s.start_s,
                end_s=s.end_s,
                start_beat_id="x",
                end_beat_id="y",
            )
            for s in p.segments
        ]
        assert validate_segments(show, segs) == [], p.id  # every timestamp traces to real beats
        assert 20 <= p.total_duration_s <= 60.0001
        assert (
            p.hook.text in beat_texts and p.cliffhanger.text in beat_texts
        )  # quotes come from source
        assert len(p.why.split()) <= 25

    rows = list(csv.DictReader((out / "promos.csv").open()))
    assert len(rows) == len(promos) and rows[0]["seg1_episode"] and rows[0]["hook_text"]
    md = (out / "ranking.md").read_text()
    assert md.count("\n## ") == min(10, len(promos)) and "Post first because" in md and "00:" in md

    rep = (out / "quality_report.md").read_text()
    for section in (
        "Validation",
        "Calibration",
        "Ablation",
        "Stability",
        "Golden set",
        "Where the tool picks badly",
    ):
        assert section in rep
    assert "Top-10 overlap" in rep and "Top-10 picks that change" in rep

    meta = json.loads((out / "run_metadata.json").read_text())
    assert meta["llm"]["provider"] == "mock" and meta["llm"]["temperature"] == 0.0
    assert set(meta["prompts"]) == {"score_v1", "clarity_v1", "coherence_v1"}
    assert meta["usage"]["calls"] > 0 and meta["measured_output_tokens_per_scored_candidate"] > 0
    assert meta["variants"]["ablation:literal_cliffhanger"] is not None and meta["partial"] is False
    assert meta["variants"]["ablation:episode_end_prior"] is not None
    assert (out / "promos_ablation_literal_cliffhanger.json").exists()
    assert (out / "promos_ablation_episode_end_prior.json").exists()
    assert set(meta["calls_by_phase"]) >= {"main", "ablation:literal_cliffhanger"}


def test_rerun_with_warm_cache_makes_zero_llm_calls(show_dir: Path, tmp_path: Path) -> None:
    cache = MemoryCache()
    first = make_llm(cache=cache)
    run(show_dir, tmp_path, llm=first, stability=False)
    second = make_llm(cache=cache)
    res = run(show_dir, tmp_path, llm=second, stability=False)
    assert len(first.records) > 0 and len(second.records) == 0
    assert res.main.result.promos


def test_quota_stop_resumes_without_rebilling(show_dir: Path, tmp_path: Path) -> None:
    base = make_llm()
    run(show_dir, tmp_path, llm=base, stability=False)
    total = len(base.records)

    cache = MemoryCache()
    first = make_llm(SpyMock(faults=["ok"] * 10 + ["daily_quota"]), cache)
    with pytest.raises(RunInterrupted) as e:
        run(show_dir, tmp_path, llm=first, stability=False)
    assert "DailyQuotaExhausted" in str(e.value) and len(first.records) == 10

    second = make_llm(SpyMock(), cache)
    res = run(show_dir, tmp_path, llm=second, stability=False)
    assert res.partial is False and res.main.result.promos
    assert len(first.records) + len(second.records) == total  # nothing billed twice


def test_allow_partial_finishes_with_rule_fallback_and_says_so(
    show_dir: Path, tmp_path: Path
) -> None:
    llm = make_llm(SpyMock(faults=["daily_quota"]))
    res = run(show_dir, tmp_path, llm=llm, allow_partial=True)
    assert res.partial and res.ablations == [] and res.stability is None
    assert res.main.result.promos
    assert all("rule_fallback_main" in p.flags for p in res.main.result.promos)
    out = write_all(res)
    assert "partial" in (out / "quality_report.md").read_text().lower()
    assert json.loads((out / "promos.json").read_text())["partial"] is True


def test_golden_set_is_scored_in_the_report(show_dir: Path, tmp_path: Path) -> None:
    first = run(show_dir, tmp_path, ablation=False, stability=False)
    top = first.main.result.promos[0]
    g = tmp_path / "golden.yaml"
    s = top.segments[0]
    g.write_text(
        f"promos:\n  - {{episode: {s.episode}, start: {s.start_s}, end: {s.end_s}, note: top pick}}\n"
        f"  - {{episode: {s.episode}, start: 0.2, end: 0.3, note: tiny}}\n"
    )
    res = run(show_dir, tmp_path, golden_path=g, ablation=False, stability=False, llm=make_llm())
    assert res.golden and res.golden.precision_at_k > 0
    assert res.golden.matches[0].best_iou == 1.0 and res.golden.matches[0].best_rank == 1
    out = write_all(res)
    assert "precision@10" in (out / "quality_report.md").read_text()


def test_ablation_and_stability_can_be_switched_off(show_dir: Path, tmp_path: Path) -> None:
    res = run(show_dir, tmp_path, ablation=False, stability=False)
    assert res.ablations == [] and res.stability is None
    out = write_all(res)
    rep = (out / "quality_report.md").read_text()
    assert "Ablations not run" in rep and "Stability not run" in rep


def test_cli_one_command(
    show_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["run", str(show_dir), "--out", str(tmp_path / "o"), "--no-cache",
               "--provider", "mock"])  # fmt: skip
    assert rc == 0
    out = capsys.readouterr().out
    assert "outputs:" in out and "LLM calls" in out
    assert list((tmp_path / "o").glob("*/*/promos.json"))


def test_cli_missing_key_is_a_clean_error(show_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    for v in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    rc = main(["run", str(show_dir), "--out", str(tmp_path / "o"), "--provider", "gemini"])
    assert rc == 2 and "GEMINI_API_KEY" in capsys.readouterr().err


def test_rules_only_mode_needs_no_llm(
    show_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        ["run", str(show_dir), "--out", str(tmp_path / "o"), "--no-cache", "--provider", "none"]
    )
    assert rc == 0
    out = next((tmp_path / "o").glob("*/*"))
    doc = json.loads((out / "promos.json").read_text())
    assert doc["partial"] is True and doc["promos"]
    assert all("rule_fallback_main" in p["flags"] for p in doc["promos"])
    assert "rules-only" in (out / "quality_report.md").read_text().lower()
