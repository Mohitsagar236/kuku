from __future__ import annotations

from pathlib import Path

import yaml
from promo_common.config import find_config_dir
from pydantic import BaseModel


class ScoringConfig(BaseModel):
    score_prompt: str = "score_v1"
    clarity_prompt: str = "clarity_v2"  # blind clarity + end-point choice (one call)
    coherence_prompt: str = "coherence_v1"
    rerank_prompt: str = "rerank_v1"
    hook_window_s: float = 3.0
    cliff_window_s: float = 8.0
    max_batch: int = 8  # candidates per scoring call (a per-episode batch is split above this)
    stitched_batch: int = 5
    clarity_batch: int = 6
    max_validation_retries: int = 2
    rerank_pool: int = 30
    rerank_passes: int = 2
    min_total_s: float = 20.0  # legal total duration for end-point alternatives
    max_total_s: float = 60.0
    end_spacing_s: float = 1.0  # offered end points are at least this far apart
    refine_ends: bool = True
    cascade_top: int = 60  # stage 2 (blind clarity + coherence) runs on this many; 0 = all
    max_words_why: int = 25
    max_words_gap: int = 12
    # ablation: literal script annotations that may leak the answer ("... abrupt cliffhanger cut")
    literal_cues: list[str] = ["cliffhanger"]


def load_scoring_config(path: Path | None = None) -> ScoringConfig:
    p = path or find_config_dir() / "pipeline.yaml"
    return ScoringConfig(**(yaml.safe_load(p.read_text()).get("scoring") or {}))
