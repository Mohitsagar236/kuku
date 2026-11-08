"""The public demo is seeded from real packaged outputs when they exist, not from mock scores."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from promo_finder.application.models import RunOptions
from promo_finder.application.pipeline import run_pipeline
from promo_finder.infrastructure.artifacts import write_all
from promo_finder.infrastructure.llm import LLMSettings
from promo_finder.interfaces.web import demo
from tests.e2e.helpers import write_show
from tests.shared import make_llm


@pytest.fixture()
def packaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for resources/demo_outputs: a finished run (the mock plays the 'real' model)."""
    show_dir = write_show(tmp_path, n_eps=3)
    src = tmp_path / "pkg"
    res = run_pipeline(
        show_dir,
        RunOptions(out_root=src, persist_cache=False, stability=False),
        LLMSettings(_env_file=None),
        llm=make_llm(),
    )
    run = write_all(res)
    (run / "llm_raw").mkdir(exist_ok=True)
    (run / "llm_raw" / "0001.json").write_text("{}")
    (run / "CONFIDENTIAL.txt").write_text("x")
    monkeypatch.setattr(demo, "DEMO_OUTPUTS", src)
    return src


def test_packaged_outputs_are_copied_and_preferred_over_mock_scores(packaged, tmp_path) -> None:
    out = demo.seed_demo(tmp_path / "root")
    runs = list(out.glob("*/*/promos.json"))
    assert runs and not any(p.parent.name.startswith("smoke") for p in runs)
    meta = json.loads(next(out.glob("*/*/run_metadata.json")).read_text())
    assert meta["llm"]["model"] != "render-demo"  # not seeded by the simulated demo model
    assert not list(out.rglob("llm_raw")) and not list(out.rglob("CONFIDENTIAL.txt"))


def test_nudges_are_pointed_at_the_packaged_demo_screenplays(packaged, tmp_path) -> None:
    out = demo.seed_demo(tmp_path / "root")
    for meta in out.glob("*/*/run_metadata.json"):
        assert json.loads(meta.read_text())["source_dir"] == str(demo.DEMO_SOURCE)


def test_seeding_twice_keeps_existing_reviews_and_data(packaged, tmp_path) -> None:
    root = tmp_path / "root"
    out = demo.seed_demo(root)
    marker = next(out.glob("*/*/promos.json")).parent / "marker.txt"
    marker.write_text("kept")
    demo.seed_demo(root)
    assert marker.read_text() == "kept"


def test_without_packaged_outputs_the_demo_falls_back_to_mock(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(demo, "DEMO_OUTPUTS", tmp_path / "nothing-here")
    out = demo.seed_demo(tmp_path / "root")
    meta = json.loads(next(out.glob("*/*/run_metadata.json")).read_text())
    assert meta["llm"]["model"] == "render-demo"


# ---- guard on the data that is actually shipped (resources/demo_outputs) ----------------------

SHIPPED = demo.DEMO_OUTPUTS


def _shipped_run() -> Path:
    (path,) = [p.parent for p in SHIPPED.glob("*/20*/promos.json")]
    return path


def test_shipped_demo_data_contains_no_mock_scores_or_text() -> None:
    assert SHIPPED.is_dir() and demo.packaged_demo_runs(), "demo_outputs must ship with the package"
    for f in SHIPPED.rglob("*"):
        if f.is_file():
            assert "Mock rationale" not in f.read_text(), f
    meta = json.loads((_shipped_run() / "run_metadata.json").read_text())
    assert meta["llm"]["provider"] not in {"mock", "none"}
    assert not meta["llm"]["model"].startswith(("mock", "render-demo"))
    assert meta["partial"] is False


@pytest.mark.parametrize(
    "name",
    [
        "promos.json",
        "promos_ablation_episode_end_prior.json",
        "promos_ablation_literal_cliffhanger.json",
    ],
)
def test_every_shipped_variant_has_real_model_scores_and_rationales(name: str) -> None:
    data = json.loads((_shipped_run() / name).read_text())
    assert not str(data["model"]).startswith(("mock", "rules-only")) and not data.get("partial")
    assert data["promos"]
    for p in data["promos"]:
        assert p["why"].strip() and "Mock" not in p["why"]
        sc = p["scores"]
        assert all(sc[k] is not None for k in ("llm_hook", "llm_cliffhanger", "llm_clarity"))


def test_shipped_demo_data_has_no_local_absolute_paths() -> None:
    for f in SHIPPED.rglob("*"):
        if f.is_file():
            text = f.read_text()
            assert "/Users/" not in text and "/private/" not in text and "/home/" not in text, f
