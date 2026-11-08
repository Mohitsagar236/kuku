"""Seed an isolated, fictional public demo with no credentials or model requests."""

from __future__ import annotations

from pathlib import Path

from promo_finder.application.models import RunOptions
from promo_finder.application.pipeline import run_pipeline
from promo_finder.infrastructure.artifacts import write_all
from promo_finder.infrastructure.llm import LLMSettings

DEMO_SOURCE = Path(__file__).resolve().parents[2] / "resources" / "demo"


def seed_demo(root: Path) -> Path:
    outputs = root / "outputs"
    # One process/worker is used on the free demo. A restart reuses only this
    # ephemeral demo root, never the local confidential outputs or provider cache.
    if any(outputs.glob("*/*/promos.json")):
        return outputs
    root.mkdir(parents=True, exist_ok=True)
    result = run_pipeline(
        DEMO_SOURCE,
        RunOptions(out_root=outputs, persist_cache=False, ablation=True, stability=False),
        LLMSettings(_env_file=None, provider="mock", model="render-demo", rpm=0, rpd=0),
    )
    result.show.title = "The Quarterly Close - Sample Story"
    try:
        write_all(result)
    finally:
        result.llm.client.close()
    return outputs
