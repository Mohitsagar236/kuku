"""Final ranked promo as delivered to editors."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PromoSegment(BaseModel):
    episode: int
    start_s: float
    end_s: float
    start_ts: str
    end_ts: str


class Moment(BaseModel):
    """A hook or cliffhanger. Text is filled in by code from the source beat, never by the LLM."""

    beat_id: str
    episode: int
    ts: str
    kind: str
    speaker: str | None = None
    text: str


class SubScores(BaseModel):
    hook: float
    cliffhanger: float
    clarity: float
    intensity: float
    spoiler_risk: float | None = None
    coherence: float | None = None
    rule_hook: float
    rule_cliffhanger: float
    rule_intensity: float
    rule_clarity: float
    llm_hook: float | None = None
    llm_cliffhanger: float | None = None
    llm_intensity: float | None = None
    llm_clarity: float | None = None
    rerank_rank: int | None = None


class Promo(BaseModel):
    rank: int
    id: str
    kind: Literal["single", "stitched"]
    segments: list[PromoSegment]
    total_duration_s: float
    hook: Moment
    cliffhanger: Moment
    why: str
    post_first_because: str = ""
    scores: SubScores
    rule_score: float
    final_score: float
    confidence: float = Field(ge=0, le=1)
    risks: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    trim_applied: bool = False
    ends_at_episode_end: bool = False
    status: Literal["proposed", "approved", "rejected", "adjusted"] = "proposed"
