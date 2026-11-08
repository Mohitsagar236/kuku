"""Consistent error model (RFC 7807 problem+json)."""

from __future__ import annotations

from pydantic import BaseModel


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None


class AppError(Exception):
    """Base class for expected, user-facing errors."""

    status = 500
    title = "Internal error"

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail

    def to_problem(self, request_id: str | None = None) -> ProblemDetail:
        return ProblemDetail(
            title=self.title, status=self.status, detail=self.detail, request_id=request_id
        )


class ValidationFailure(AppError):
    status = 422
    title = "Validation failed"


class NotFound(AppError):
    status = 404
    title = "Not found"


class IngestionError(ValidationFailure):
    title = "Ingestion failed"
