"""LLM error taxonomy. Transient errors are retried; the rest stop the call or the run."""

from __future__ import annotations


class LLMError(Exception):
    """Base class; not retried."""


class LLMConfigError(LLMError, ValueError):
    """Bad or missing LLM settings in .env (unknown provider, no base URL, bad header JSON)."""


class LLMTransientError(LLMError):
    """Timeouts, 5xx, connection resets: retried with exponential backoff.

    ``kind`` says which one, so every retry can be recorded: ``timeout``, ``transport:<Type>``,
    ``http_5xx:<code>``, ``http_408`` or ``429_rate_limit``.
    """

    def __init__(self, message: str, kind: str = "transient") -> None:
        super().__init__(message)
        self.kind = kind


class LLMRateLimitError(LLMTransientError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message, kind="429_rate_limit")
        self.retry_after = retry_after


class DailyQuotaExhausted(LLMError):
    """Daily request quota used up (client-side RPD or provider 429 'per day'). Run is resumable."""


class LLMAuthError(LLMError):
    """Missing/invalid API key."""


class BudgetExceeded(LLMError):
    """Per-job spend/call cap reached."""


class InvalidLLMOutput(LLMError):
    """Response is not valid JSON / does not match the expected schema."""
