from __future__ import annotations

from typing import Any

from promo_common.llm.base import LLMRequest, LLMResponse
from promo_common.llm.providers.http import HttpProvider


class GeminiProvider(HttpProvider):
    provider = "gemini"
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    thinking_budget: int | None = None  # 0 = no thinking tokens (they eat max output tokens)
    thinking_level: str | None = (
        None  # Gemini 3.x control ("minimal" ...); budget is the 2.5 control
    )
    output_headroom = (
        2048  # thinking tokens count against maxOutputTokens: never let them cut the JSON
    )
    send_thinking = True

    def _adapt(self, body: str) -> bool:
        low = body.lower()
        # Some models (e.g. 3.x flash-lite) reject thinkingBudget with a bare INVALID_ARGUMENT and
        # no hint. Drop the optional thinking config first, then fall back to the temperature rule.
        if (
            self.send_thinking
            and (self.thinking_budget is not None or self.thinking_level)
            and ("thinking" in low or "invalid argument" in low or "invalid_argument" in low)
        ):
            self.send_thinking = False
            return True
        return super()._adapt(body)

    def _send(self, req: LLMRequest, with_temperature: bool) -> dict[str, Any]:
        gen: dict[str, Any] = {"maxOutputTokens": req.max_tokens + self.output_headroom}
        if with_temperature:
            gen["temperature"] = req.temperature
        if req.json_mode:
            gen["responseMimeType"] = "application/json"
        if self.send_thinking:
            if self.thinking_budget is not None:
                gen["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
            elif self.thinking_level:
                gen["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        body = {
            "systemInstruction": {"parts": [{"text": req.system}]},
            "contents": [{"role": "user", "parts": [{"text": req.user}]}],
            "generationConfig": gen,
        }
        headers = {"x-goog-api-key": self._require_key()}
        return self._post(f"{self.base_url}/models/{self.model}:generateContent", headers, body)

    def complete(self, req: LLMRequest) -> LLMResponse:
        data, ms = self._timed(req)
        usage = data.get("usageMetadata") or {}
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        out = int(usage.get("candidatesTokenCount", 0)) + int(usage.get("thoughtsTokenCount", 0))
        return LLMResponse(
            text=text,
            tokens_in=int(usage.get("promptTokenCount", 0)),
            tokens_out=out,
            latency_ms=ms,
            model=data.get("modelVersion", self.model),
            provider=self.provider,
            truncated=(data.get("candidates") or [{}])[0].get("finishReason") == "MAX_TOKENS",
        )
