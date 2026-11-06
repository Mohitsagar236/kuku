"""LLM-produced scores for a candidate (always keyed to FULL beat ids, never text/timestamps)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LLMScores(BaseModel):
    # hook / cliffhanger / intensity / spoiler: the context-aware judge ("score" task)
    main_ok: bool = False
    hook: float | None = Field(default=None, ge=0, le=10)
    cliffhanger: float | None = Field(default=None, ge=0, le=10)
    intensity: float | None = Field(default=None, ge=0, le=10)
    spoiler_risk: float | None = Field(default=None, ge=0, le=10)
    hook_beat_id: str | None = None
    cliff_beat_id: str | None = None
    trim_start_beat_id: str | None = None
    trim_end_beat_id: str | None = None
    why: str = ""
    stage2: bool = True  # False: skipped by the cascade (no blind clarity / coherence judged)
    # blind standalone clarity (sees the clip only)
    clarity_ok: bool = False
    clarity: float | None = Field(default=None, ge=0, le=10)
    clarity_gap: str = ""
    # stitched candidates only
    coherence_ok: bool | None = None
    coherence: float | None = Field(default=None, ge=0, le=10)
    coherence_gap: str = ""
    errors: list[str] = Field(default_factory=list)
