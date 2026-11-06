"""OpenAI and Groq (OpenAI-compatible chat completions)."""

from __future__ import annotations

from typing import Any

from promo_common.llm.base import LLMRequest, LLMResponse
from promo_common.llm.providers.http import HttpProvider


class OpenAICompatProvider(HttpProvider):
    provider = "openai"
    base_url = "https://api.openai.com/v1"
    token_param = "max_completion_tokens"

    def _send(self, req: LLMRequest, with_temperature: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            self.token_param: req.max_tokens,
        }
        if with_temperature:
            body["temperature"] = req.temperature
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._require_key()}"}
        return self._post(f"{self.base_url}/chat/completions", headers, body)

    def complete(self, req: LLMRequest) -> LLMResponse:
        data, ms = self._timed(req)
        usage = data.get("usage") or {}
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return LLMResponse(
            text=text,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=ms,
            model=data.get("model", self.model),
            provider=self.provider,
            truncated=(data.get("choices") or [{}])[0].get("finish_reason") == "length",
        )


class GroqProvider(OpenAICompatProvider):
    provider = "groq"
    base_url = "https://api.groq.com/openai/v1"
    token_param = "max_tokens"
