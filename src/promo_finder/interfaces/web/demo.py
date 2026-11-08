"""Seed an isolated, fictional public demo. The server itself never calls a model or needs a key.

Preferred data: real outputs generated once offline (``scripts/build_demo_data.py``) and shipped in
``resources/demo_outputs``. They are copied into the demo's ephemeral root at start-up. If they
are absent (a fresh checkout), the demo falls back to simulated mock scores so it still runs.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from promo_finder.application.models import RunOptions
from promo_finder.application.pipeline import run_pipeline
from promo_finder.infrastructure.artifacts import write_all
from promo_finder.infrastructure.llm import LLMSettings

RESOURCES = Path(__file__).resolve().parents[2] / "resources"
DEMO_SOURCE = RESOURCES / "demo"
DEMO_OUTPUTS = RESOURCES / "demo_outputs"
DEMO_TITLE = "The Quarterly Close - Sample Story"
SKIP_DIRS = {"llm_raw"}  # raw model replies stay out of the shipped demo
SKIP_FILES = {".gitkeep", "CONFIDENTIAL.txt"}  # the demo is fictional: no confidentiality notice


def packaged_demo_runs(src: Path | None = None) -> list[Path]:
    src = DEMO_OUTPUTS if src is None else src  # looked up at call time, not import time
    return sorted(src.glob("*/*/promos.json")) if src.is_dir() else []


def _install_packaged(src: Path, outputs: Path) -> None:
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if item.is_dir() or SKIP_DIRS & set(rel.parts) or item.name in SKIP_FILES:
            continue
        dest = outputs / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, dest)
    # Cut nudges re-validate against the screenplay: point each run at the packaged demo source.
    for meta in outputs.glob("*/*/run_metadata.json"):
        data = json.loads(meta.read_text())
        data["source_dir"] = str(DEMO_SOURCE)
        meta.write_text(json.dumps(data, indent=2))


def seed_demo(root: Path) -> Path:
    outputs = root / "outputs"
    # One process/worker is used on the free demo. A restart reuses only this
    # ephemeral demo root, never the local confidential outputs or provider cache.
    if any(outputs.glob("*/*/promos.json")):
        return outputs
    root.mkdir(parents=True, exist_ok=True)
    if packaged_demo_runs(DEMO_OUTPUTS):
        _install_packaged(DEMO_OUTPUTS, outputs)
        return outputs
    result = run_pipeline(
        DEMO_SOURCE,
        RunOptions(out_root=outputs, persist_cache=False, ablation=True, stability=False),
        LLMSettings(_env_file=None, provider="mock", model="render-demo", rpm=0, rpd=0),
    )
    result.show.title = DEMO_TITLE
    try:
        write_all(result)
    finally:
        result.llm.client.close()
    return outputs
