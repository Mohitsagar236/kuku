"""Strict response schemas for each judge. Unknown extra keys are ignored, bad values rejected."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ScoreItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    hook: str
    cliff: str
    h: int = Field(ge=0, le=10)
    c: int = Field(ge=0, le=10)
    i: int = Field(ge=0, le=10)
    s: int = Field(ge=0, le=10)
    why: str = ""
    ts: str | None = None
    te: str | None = None


class ClarityItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    clarity: int = Field(ge=0, le=10)
    gap: str = ""


class RefineItem(BaseModel):
    """Blind clarity + the clip's best end point, in one answer."""

    model_config = ConfigDict(extra="ignore")
    id: str
    clarity: int = Field(ge=0, le=10)
    end: str | None = None
    cliff: int | None = Field(default=None, ge=0, le=10)
    gap: str = ""


class CoherenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    coherence: int = Field(ge=0, le=10)
    gap: str = ""


def cap_words(text: str | None, n: int) -> str:
    words = (text or "").split()
    return " ".join(words[:n])
