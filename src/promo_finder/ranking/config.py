from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from promo_finder.infrastructure.config import find_config_dir


class Weights(BaseModel):
    hook: float = 0.30
    cliffhanger: float = 0.30
    clarity: float = 0.20
    intensity: float = 0.20


class Fusion(BaseModel):
    llm_share: float = 0.7
    clarity_llm_share: float = 0.85
    weights: Weights = Weights()
    spoiler_penalty_per_point: float = 0.05
    stitched_base: float = 0.70
    stitched_unknown: float = 0.80


class Selection(BaseModel):
    dedupe_iou: float = 0.30
    same_ending_tol_s: float = 1.5
    max_per_episode: int = 2
    max_per_scene: int = 1
    max_per_arc: int = 5
    arc_size: int = 5
    output_count: int = 30


class RerankCfg(BaseModel):
    weight: float = 0.5
    pool: int = 30


class TrimCfg(BaseModel):
    apply: bool = True


class ConfidenceCfg(BaseModel):
    base_with_llm: float = 0.55
    agreement_gain: float = 0.40
    no_llm: float = 0.25


class Gates(BaseModel):
    """Hard gates on the LLM's own scores: below the threshold a candidate is never delivered."""

    min_hook: float = 5.0
    min_cliffhanger: float = 5.0


class RankingConfig(BaseModel):
    gates: Gates = Gates()
    fusion: Fusion = Fusion()
    selection: Selection = Selection()
    rerank: RerankCfg = RerankCfg()
    trim: TrimCfg = TrimCfg()
    confidence: ConfidenceCfg = ConfidenceCfg()


def load_ranking_config(path: Path | None = None) -> RankingConfig:
    p = path or find_config_dir() / "weights.yaml"
    return RankingConfig(**yaml.safe_load(p.read_text()))
