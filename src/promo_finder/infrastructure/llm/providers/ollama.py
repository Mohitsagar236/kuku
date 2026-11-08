from __future__ import annotations

from typing import Any

from promo_finder.infrastructure.llm.base import LLMRequest, LLMResponse
from promo_finder.infrastructure.llm.providers.http import HttpProvider


class OllamaProvider(HttpProvider):
    provider = "ollama"
    base_url = "http://localhost:11434"

    def _send(self, req: LLMRequest, with_temperature: bool) -> dict[str, Any]:
        options: dict[str, Any] = {"num_predict": req.max_tokens}
        if with_temperature:
            options["temperature"] = req.temperature
        body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            "options": options,
        }
        if req.json_mode:
            body["format"] = "json"
        return self._post(f"{self.base_url}/api/chat", {}, body)

    def complete(self, req: LLMRequest) -> LLMResponse:
        data, ms = self._timed(req)
        return LLMResponse(
            text=(data.get("message") or {}).get("content", ""),
            tokens_in=int(data.get("prompt_eval_count", 0)),
            tokens_out=int(data.get("eval_count", 0)),
            latency_ms=ms,
            model=data.get("model", self.model),
            provider=self.provider,
            truncated=data.get("done_reason") == "length",
        )
