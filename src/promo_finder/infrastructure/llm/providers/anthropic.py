from __future__ import annotations

from typing import Any

from promo_finder.infrastructure.llm.base import LLMRequest, LLMResponse
from promo_finder.infrastructure.llm.providers.http import HttpProvider


class AnthropicProvider(HttpProvider):
    provider = "anthropic"
    base_url = "https://api.anthropic.com/v1"

    def _send(self, req: LLMRequest, with_temperature: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
        }
        if with_temperature:
            body["temperature"] = req.temperature
        headers = {"x-api-key": self._require_key(), "anthropic-version": "2023-06-01"}
        return self._post(f"{self.base_url}/messages", headers, body)

    def complete(self, req: LLMRequest) -> LLMResponse:
        data, ms = self._timed(req)
        usage = data.get("usage") or {}
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        return LLMResponse(
            text=text,
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            latency_ms=ms,
            model=data.get("model", self.model),
            provider=self.provider,
            truncated=data.get("stop_reason") == "max_tokens",
        )
