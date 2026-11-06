"""Normalized timeline: show -> episodes -> scenes -> beats."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BeatType(StrEnum):
    DIALOGUE = "dialogue"
    ACTION = "action"
    SOUND_FX = "sound_fx"
    CAMERA = "camera"
    OTHER = "other"


class ParseWarning(BaseModel):
    """A non-fatal problem found while parsing. Never raised, always recorded."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    line: int | None = None


class Beat(BaseModel):
    """One timed unit of script: a line of dialogue, an action, a sound cue or a shot."""

    id: str = Field(description="Stable id, e.g. E003-S01-B007")
    episode_number: int
    scene_idx: int
    seq: int = Field(ge=0, description="Order within the scene, by start time")
    type: BeatType
    speaker: str | None = Field(default=None, description="Normalized speaker name")
    speaker_raw: str | None = Field(default=None, description="Label exactly as in the script")
    emotion_tags: list[str] = Field(default_factory=list)
    voice_over: bool = False
    off_screen: bool = False
    text: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Beat:
        if self.end_s < self.start_s:
            raise ValueError(f"beat {self.id}: end_s {self.end_s} < start_s {self.start_s}")
        return self

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class Scene(BaseModel):
    idx: int
    heading: str = ""
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    beats: list[Beat] = Field(default_factory=list)


class Episode(BaseModel):
    number: int
    source_file: str
    logline: str = ""
    continuity_notes: str = ""
    duration_s: float = Field(ge=0)
    scenes: list[Scene] = Field(default_factory=list)
    warnings: list[ParseWarning] = Field(default_factory=list)

    @property
    def beats(self) -> list[Beat]:
        """All beats in the episode, ordered by (start, scene, seq)."""
        return sorted(
            (b for s in self.scenes for b in s.beats),
            key=lambda b: (b.start_s, b.scene_idx, b.seq),
        )


class Show(BaseModel):
    slug: str
    title: str
    source_hash: str = Field(description="sha256 over all input file bytes, order-stable")
    episodes: list[Episode] = Field(default_factory=list)
    speaker_aliases: dict[str, str] = Field(default_factory=dict)
    warnings: list[ParseWarning] = Field(default_factory=list)

    def episode(self, number: int) -> Episode | None:
        return next((e for e in self.episodes if e.number == number), None)
