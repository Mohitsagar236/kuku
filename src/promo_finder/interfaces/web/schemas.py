"""Strict HTTP contracts; malformed writes never reach the editor service."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    promo_id: str = Field(min_length=1, max_length=128)
    verdict: Literal["approve", "would_post", "reject", "clear"] | None = None
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def has_change(self) -> "ReviewRequest":
        if self.verdict is None and self.note is None:
            raise ValueError("give a verdict and/or a note")
        return self


class NudgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    promo_id: str = Field(min_length=1, max_length=128)
    segment: int | None = Field(default=None, ge=0)
    edge: Literal["start", "end"] | None = None
    delta: float | None = None
    to: float | None = None
    reset: bool = False

    @model_validator(mode="after")
    def valid_move(self) -> "NudgeRequest":
        if self.reset:
            if self.model_fields_set - {"promo_id", "reset"}:
                raise ValueError("reset cannot be combined with a move")
        elif self.segment is None or self.edge is None or (self.delta is None) == (self.to is None):
            raise ValueError("give segment, edge and exactly one of delta or to")
        return self
