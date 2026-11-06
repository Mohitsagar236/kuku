"""Shared HTTP plumbing for REST providers (httpx; no vendor SDKs)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from promo_common.llm.base import LLMClient, LLMRequest
from promo_common.llm.errors import (
    DailyQuotaExhausted,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
)


class BadRequest(LLMError):
    def __init__(self, body: str) -> None:
        super().__init__(f"bad request: {body[:300]}")
        self.body = body


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


class HttpProvider(LLMClient):
    base_url = ""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 90.0,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or self.base_url).rstrip("/")
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self.send_temperature = True

    def _require_key(self) -> str:
        if not self.api_key:
            raise LLMAuthError(f"{self.provider}: API key not set")
        return self.api_key

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._http.post(url, headers=headers, json=body)
        except httpx.TimeoutException as e:
            raise LLMTransientError(f"{self.provider}: timeout", kind="timeout") from e
        except httpx.TransportError as e:
            raise LLMTransientError(
                f"{self.provider}: transport error: {type(e).__name__}",
                kind=f"transport:{type(e).__name__}",
            ) from e
        if r.status_code == 200:
            data: dict[str, Any] = r.json()
            return data
        text = r.text
        if r.status_code == 429:
            low = text.lower()
            if (
                "perday" in low.replace(" ", "").replace("_", "")
                or "per day" in low
                or "daily" in low
            ):
                raise DailyQuotaExhausted(f"{self.provider}: daily quota exhausted")
            raise LLMRateLimitError(f"{self.provider}: 429", _retry_after(r))
        if r.status_code in (401, 403):
            raise LLMAuthError(f"{self.provider}: auth failed ({r.status_code})")
        if r.status_code >= 500 or r.status_code == 408:
            kind = "http_408" if r.status_code == 408 else f"http_5xx:{r.status_code}"
            raise LLMTransientError(f"{self.provider}: HTTP {r.status_code}", kind=kind)
        raise BadRequest(text)

    def _adapt(self, body: str) -> bool:
        """After a 400, switch off an optional parameter the model rejected. True if changed."""
        if self.send_temperature and "temperature" in body.lower():
            self.send_temperature = False
            return True
        return False

    def _timed(self, req: LLMRequest) -> tuple[dict[str, Any], float]:
        """Build+send; on 400 drop the optional parameter the model rejected and resend."""
        t0 = time.perf_counter()
        for _ in range(3):
            try:
                data = self._send(req, self.send_temperature)
                return data, (time.perf_counter() - t0) * 1000
            except BadRequest as e:
                if not self._adapt(e.body):
                    raise
        data = self._send(req, self.send_temperature)
        return data, (time.perf_counter() - t0) * 1000

    def _send(self, req: LLMRequest, with_temperature: bool) -> dict[str, Any]:
        raise NotImplementedError
