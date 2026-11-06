from __future__ import annotations

import json
import zipfile
from pathlib import Path

import httpx
import pytest
from ingestion_service import load_show, parse_episode_spec
from promo_common.errors import IngestionError
from promo_common.llm import LLMRequest, LLMSettings
from promo_common.llm.providers.anthropic import AnthropicProvider
from promo_common.llm.providers.gemini import GeminiProvider
from promo_common.llm.providers.ollama import OllamaProvider
from promo_common.llm.providers.openai_compat import OpenAICompatProvider
from promo_finder_cli import pipeline as pl
from promo_finder_cli.__main__ import main
from promo_finder_cli.outputs import write_all
from promo_finder_cli.package import package_outputs
from promo_finder_cli.pipeline import RunOptions, run_pipeline
from promo_finder_cli.smoke import smoke_report
from scoring_service import Scorer, load_scoring_config

from tests.e2e.helpers import write_show
from tests.shared import SpyMock, make_llm


@pytest.fixture()
def show_dir(tmp_path: Path) -> Path:
    return write_show(tmp_path, n_eps=8)


def run(show_dir: Path, tmp_path: Path, **kw):
    llm = kw.pop("llm", None) or make_llm()
    opts = RunOptions(out_root=tmp_path / "out", persist_cache=False, **kw)
    return run_pipeline(show_dir, opts, LLMSettings(_env_file=None), llm=llm)


# ---- episodes filter -----------------------------------------------------------------------
def test_episode_spec_parsing_and_filter(show_dir: Path) -> None:
    assert parse_episode_spec("1-3") == {1, 2, 3}
    assert parse_episode_spec("1,4,6-7") == {1, 4, 6, 7}
    assert parse_episode_spec(None) is None and parse_episode_spec("all") is None
    for bad in ("x", "3-1", "0-2"):
        with pytest.raises(IngestionError):
            parse_episode_spec(bad)
    sub = load_show(show_dir, episodes={2, 3})
    full = load_show(show_dir)
    assert [e.number for e in sub.episodes] == [2, 3] and sub.source_hash != full.source_hash
    with pytest.raises(IngestionError, match="no matching episodes"):
        load_show(show_dir, episodes={99})


# ---- truncation detection in every adapter ---------------------------------------------------
REQ = LLMRequest("s", "u")


def _t(h):
    return httpx.MockTransport(h)


def test_every_adapter_reports_truncation() -> None:
    oa = OpenAICompatProvider("m", "k", transport=_t(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": "{"}, "finish_reason": "length"}], "usage": {}})))  # fmt: skip
    an = AnthropicProvider("m", "k", transport=_t(lambda r: httpx.Response(200, json={
        "content": [{"type": "text", "text": "{"}], "stop_reason": "max_tokens", "usage": {}})))  # fmt: skip
    ge = GeminiProvider("m", "k", transport=_t(lambda r: httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": "{"}]}, "finishReason": "MAX_TOKENS"}],
        "usageMetadata": {}})))  # fmt: skip
    ol = OllamaProvider("m", None, transport=_t(lambda r: httpx.Response(200, json={
        "message": {"content": "{"}, "done_reason": "length"})))  # fmt: skip
    assert all(p.complete(REQ).truncated for p in (oa, an, ge, ol))
    ok = OpenAICompatProvider("m", "k", transport=_t(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}})))  # fmt: skip
    assert not ok.complete(REQ).truncated


# ---- smoke ----------------------------------------------------------------------------------
def test_smoke_cli_prints_health_counters(show_dir: Path, tmp_path: Path, capsys) -> None:
    rc = main(["smoke", str(show_dir), "--episodes", "1-2", "--provider", "mock", "--no-cache",
               "--out", str(tmp_path / "o")])  # fmt: skip
    out = capsys.readouterr().out
    assert rc == 0 and "=== SMOKE RESULT ===" in out and "VERDICT: OK" in out
    for needle in ("LLM calls made", "scoring output tokens per candidate", "JSON problems",
                   "truncated responses: 0", "daily quota used"):  # fmt: skip
        assert needle in out, needle
    run_dir = next((tmp_path / "o").glob("*/smoke-*"))
    meta = json.loads((run_dir / "run_metadata.json").read_text())
    assert (
        meta["mode"] == "smoke"
        and meta["episodes_requested"] == [1, 2]
        and meta["show"]["episodes"] == 2
    )
    assert not list((tmp_path / "o").glob("*/rules_baseline"))  # a subset must not write a baseline
    assert "ZZ" not in out and "Who are you" not in out  # counters only, no script text


def test_smoke_flags_truncation_and_json_problems(show_dir: Path, tmp_path: Path) -> None:
    spy = SpyMock(faults=["truncated", "invalid_json"])
    res = run(show_dir, tmp_path, llm=make_llm(spy), mode="smoke", ablation=False, stability=False,
              episodes={1, 2}, write_baseline=False)  # fmt: skip
    text, ok = smoke_report(res)
    assert not ok and "PROBLEMS FOUND" in text and "truncated" in text.lower()
    assert "unparseable replies" in text
    assert res.llm.summary()["truncated_calls"] == 1
    assert all(s.main_ok for s in res.main.scores.values())  # and the pipeline still recovered


# ---- cascade --------------------------------------------------------------------------------
def test_cascade_runs_blind_judges_only_on_the_shortlist(
    show_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    scfg = load_scoring_config().model_copy(update={"cascade_top": 8})
    monkeypatch.setattr(pl, "load_scoring_config", lambda: scfg)
    spy = SpyMock()
    res = run(show_dir, tmp_path, llm=make_llm(spy), ablation=False, stability=False)
    main_ = res.main
    stage2 = [c for c in main_.cands if main_.scores[c.id].stage2]
    assert 0 < len(stage2) <= 8 < len(main_.cands)
    clarity_cands = sum(len(r.meta["candidates"]) for r in spy.requests if r.task == "clarity")
    score_cands = sum(len(r.meta["candidates"]) for r in spy.requests if r.task == "score")
    assert clarity_cands == len(stage2) and score_cands >= len(main_.cands)
    ids = {p.id for p in main_.result.promos}
    assert ids <= {c.id for c in stage2}  # only fully scored candidates can be delivered
    assert all(
        main_.scores[c.id].main_ok and not main_.scores[c.id].clarity_ok
        for c in main_.cands
        if c not in stage2
    )


def test_cascade_saves_calls_versus_scoring_everything(
    show_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    def calls(top: int) -> int:
        scfg = load_scoring_config().model_copy(update={"cascade_top": top})
        monkeypatch.setattr(pl, "load_scoring_config", lambda: scfg)
        llm = make_llm()
        run(show_dir, tmp_path, llm=llm, ablation=False, stability=False)
        return len(llm.records)

    assert calls(6) < calls(0)


def test_scorer_shortlist_hook_marks_stage2(show, cands, scfg) -> None:
    pick = [c.id for c in cands[:5]]
    spy = SpyMock()
    out = Scorer(make_llm(spy), show, scfg).score(cands, shortlist=lambda cs, interim: pick)
    assert [i for i, s in out.items() if s.stage2] == pick or set(
        i for i, s in out.items() if s.stage2
    ) == set(pick)
    assert all(out[i].clarity_ok for i in pick)
    assert not any(out[c.id].clarity_ok for c in cands if c.id not in pick)
    assert sum(len(r.meta["candidates"]) for r in spy.requests if r.task == "clarity") == 5


# ---- episode-end prior ----------------------------------------------------------------------
def test_episode_end_weight_is_configurable_and_lower_than_before(cfg, lex) -> None:
    from candidate_service.context import EpCtx
    from candidate_service.signals import make_view, score_cliff

    from tests.candidate.conftest import D, make_episode

    base = [(D, i * 4.0, i * 4.0 + 2.5, f"statement number {i} is calm") for i in range(20)]
    ctx = EpCtx(make_episode(1, base + [(D, 80, 82, "It is done.")]), cfg)
    view = make_view(ctx, 50.0, 82.0)
    on = score_cliff(view, lex, cfg).score
    off = score_cliff(view, lex, cfg.model_copy(update={"episode_end_weight": 0.0})).score
    assert cfg.episode_end_weight == 0.25 and on - off == pytest.approx(2.5, abs=0.01)


def test_both_ablations_run_and_report_episode_end_share(show_dir: Path, tmp_path: Path) -> None:
    res = run(show_dir, tmp_path, stability=False)
    names = [s.name for s, _ in res.ablations]
    assert names == ["literal_cliffhanger", "episode_end_prior"]
    prior_off = dict((s.name, v) for s, v in res.ablations)["episode_end_prior"]
    assert prior_off.info["end_weight"] == 0.0 and res.main.info["end_weight"] > 0
    out = write_all(res)
    rep = (out / "quality_report.md").read_text()
    assert "### episode_end_prior" in rep and "### literal_cliffhanger" in rep
    assert "ending at an episode end" in rep and "with vs" in rep and "No cap is applied" in rep
    assert all(isinstance(p.ends_at_episode_end, bool) for p in res.main.result.promos)


# ---- rules baseline -------------------------------------------------------------------------
def test_baseline_written_to_outputs_and_compared_in_report(show_dir: Path, tmp_path: Path) -> None:
    class Inverting(SpyMock):
        """An LLM that disagrees with the rules, so promotions and demotions must show up."""

        def _score(self, meta, noise, fault):
            out = super()._score(meta, noise, fault)
            for r in out["results"]:
                r["h"], r["c"] = 10 - r["h"], 10 - r["c"]
            return out

    res = run(show_dir, tmp_path, llm=make_llm(Inverting()), ablation=False, stability=False)
    out = write_all(res)
    base = out.parent / "rules_baseline"
    for f in ("promos.json", "promos.csv", "ranking.md", "CONFIDENTIAL.txt"):
        assert (base / f).stat().st_size > 20, f
    doc = json.loads((base / "promos.json").read_text())
    assert doc["variant"] == "rules_baseline" and doc["promos"]
    assert all("rule_fallback_main" in p["flags"] for p in doc["promos"])
    rep = (out / "quality_report.md").read_text()
    assert (
        "Rules baseline vs rules + LLM" in rep
        and "Top-10 overlap, LLM run vs rules-only baseline" in rep
    )
    assert "Promoted by the LLM" in rep and "Demoted by the LLM" in rep
    section = rep.split("## 5b.")[1].split("## 6.")[0]
    assert "LLM hook" in section or "LLM cliffhanger" in section  # says WHAT moved and why
    meta = json.loads((out / "run_metadata.json").read_text())
    assert meta["rules_baseline_dir"] and meta["variants"]["rules_baseline"]["promos"] > 0


def test_subset_run_does_not_overwrite_the_full_baseline(show_dir: Path, tmp_path: Path) -> None:
    full = run(show_dir, tmp_path, ablation=False, stability=False)
    write_all(full)
    before = (full.out_dir.parent / "rules_baseline" / "promos.json").read_text()
    sub = run(
        show_dir, tmp_path, ablation=False, stability=False, episodes={1, 2}, write_baseline=False
    )
    write_all(sub)
    assert (full.out_dir.parent / "rules_baseline" / "promos.json").read_text() == before


# ---- packaging ------------------------------------------------------------------------------
def test_package_outputs_zips_latest_run_and_baseline(show_dir: Path, tmp_path: Path) -> None:
    res = run(show_dir, tmp_path, ablation=False, stability=False)
    out = write_all(res)
    smoke = run(
        show_dir,
        tmp_path,
        ablation=False,
        stability=False,
        mode="smoke",
        episodes={1, 2},
        write_baseline=False,
    )
    write_all(smoke)
    dest = package_outputs(tmp_path / "out")
    assert dest.parent.name == "packages" and dest.suffix == ".zip" and "smoke" not in dest.name
    names = zipfile.ZipFile(dest).namelist()
    assert "CONFIDENTIAL.txt" in names
    for f in ("promos.json", "promos.csv", "ranking.md", "quality_report.md", "run_metadata.json"):
        assert f"{out.name}/{f}" in names
    assert any(n.startswith(f"{out.name}/llm_raw/") for n in names)
    assert "rules_baseline/promos.json" in names
    with pytest.raises(FileNotFoundError):
        package_outputs(tmp_path / "nothing")


def test_package_cli(show_dir: Path, tmp_path: Path, capsys) -> None:
    write_all(run(show_dir, tmp_path, ablation=False, stability=False))
    assert main(["package", "--out", str(tmp_path / "out")]) == 0
    assert "never commit" in capsys.readouterr().out.lower()
    assert main(["package", "--out", str(tmp_path / "empty")]) == 2
