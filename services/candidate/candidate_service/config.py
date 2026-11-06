"""Candidate-service configuration (YAML) and the keyword lexicon."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from promo_common.config import find_config_dir
from pydantic import BaseModel


class Weights(BaseModel):
    hook: float = 0.35
    cliffhanger: float = 0.35
    intensity: float = 0.15
    clarity: float = 0.15


class CandidateConfig(BaseModel):
    min_total_s: float = 20.0
    max_total_s: float = 60.0
    hook_window_s: float = 3.0
    min_segment_s: float = 3.0
    max_segments: int = 3
    nms_iou: float = 0.5
    nms_edge_tol_s: float = 1.5
    pool_per_episode: int = 24
    top_n_per_episode: int = 8
    stitch_top_n_total: int = 40
    tail_window_s: float = 8.0
    episode_end_weight: float = 0.25  # prior for windows cut at/near the episode end; 0 = ablated
    cue_window_s: float = 8.0
    hook_seeds_per_episode: int = 3
    cliff_seeds_per_episode: int = 3
    max_episode_gap: int = 1
    min_gap_same_episode_s: float = 2.0
    seed_min_hook: float = 3.0
    seed_min_cliff: float = 3.0
    seed_spacing_s: float = 6.0
    stitch_penalty: float = 0.7
    recap_head_s: float = 30.0
    recap_tail_s: float = 60.0
    recap_min_words: int = 3
    recap_similarity: float = 0.85
    recap_overlap: float = 0.6
    weights: Weights = Weights()
    dead_air_s: float = 6.0


def _rx(words: list[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.I)


class Lexicon:
    def __init__(self, raw: dict[str, list[str]], drop_cues: list[str] | None = None) -> None:
        drop = {d.lower() for d in (drop_cues or [])}
        self.dropped_cues = sorted(drop)
        self.action = _rx(raw["action_verbs"])
        self.reveal = _rx(raw["reveal_words"])
        self.betrayal = _rx(raw["betrayal_words"])
        self.threat = [re.compile(p, re.I) for p in raw["threat_patterns"]]
        self.accusation = [re.compile(p, re.I) for p in raw["accusation_patterns"]]
        self.intense = {w.lower() for w in raw["intense_emotions"]}
        self.cue_strong = _rx([w for w in raw["cliffhanger_cues_strong"] if w.lower() not in drop])
        self.cue_weak = _rx([w for w in raw["cliffhanger_cues_weak"] if w.lower() not in drop])
        self.ending = _rx(raw["ending_action_words"])
        self.resolution = _rx(raw["resolution_phrases"])

    def is_intense_tag(self, tag: str) -> bool:
        t = tag.lower()
        return t in self.intense or any(w in self.intense for w in t.split())


def load_config(path: Path | None = None) -> CandidateConfig:
    p = path or find_config_dir() / "pipeline.yaml"
    data = yaml.safe_load(p.read_text())
    return CandidateConfig(**data.get("candidate", {}))


def load_lexicon(path: Path | None = None, drop_cues: list[str] | None = None) -> Lexicon:
    p = path or find_config_dir() / "keywords.yaml"
    return Lexicon(yaml.safe_load(p.read_text()), drop_cues)
