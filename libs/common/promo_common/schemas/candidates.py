"""Candidate promo windows and their rule-based signals."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Segment(BaseModel):
    """One continuous stretch of ONE episode. Timestamps are relative to that episode."""

    episode_number: int
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    start_beat_id: str
    end_beat_id: str

    @model_validator(mode="after")
    def _ordered(self) -> Segment:
        if self.end_s <= self.start_s:
            raise ValueError(f"segment end {self.end_s} <= start {self.start_s}")
        return self

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class RuleSignals(BaseModel):
    """Rule-based sub-scores (0-10) plus the evidence behind them."""

    hook: float = Field(ge=0, le=10)
    cliffhanger: float = Field(ge=0, le=10)
    intensity: float = Field(ge=0, le=10)
    clarity: float = Field(ge=0, le=10)
    hook_beat_id: str | None = None
    cliff_beat_id: str | None = None
    hook_terms: list[str] = Field(default_factory=list)
    cliff_cues: list[str] = Field(default_factory=list)
    ends_at_episode_end: bool = False
    episode_end_gap_s: float = 0.0
    max_dead_air_s: float = 0.0
    n_speakers: int = 0
    opens_with_recap: bool = False
    continuity_dependent: bool = False
    risks: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    id: str
    kind: Literal["single", "stitched"]
    segments: list[Segment] = Field(min_length=1, max_length=3)
    duration_s: float = Field(description="Sum of segment durations")
    rule_score: float = Field(ge=0, le=10)
    signals: RuleSignals
    content_hash: str = Field(description="sha256 of the beat ids+text, for LLM caching")
    duplicate_of: list[str] = Field(default_factory=list, description="ids removed as recap dupes")

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode_number for s in self.segments})
