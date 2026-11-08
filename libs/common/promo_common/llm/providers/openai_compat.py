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


class OpenAICompatibleGateway(OpenAICompatProvider):
    """Any server that speaks OpenAI chat completions: hosted models, gateways, vLLM, LiteLLM...

    Configured only by env: LLM_BASE_URL, LLM_MODEL, optional LLM_API_KEY (sent as a Bearer
    token when set) and LLM_EXTRA_HEADERS (a JSON object, e.g. an org or routing header).
    Gateways differ, so a 400 that names an optional parameter switches it off and retries:
    ``max_tokens`` -> ``max_completion_tokens``, then JSON mode off, then temperature off.
    """

    provider = "openai_compatible"
    token_param = "max_tokens"

    def __init__(
        self,
        model: str,
        api_key: str | None,
        base_url: str,
        extra_headers: dict[str, str] | None = None,
        timeout_s: float = 90.0,
        transport: Any = None,
    ) -> None:
        super().__init__(model, api_key, timeout_s, base_url=base_url, transport=transport)
        self.extra_headers = dict(extra_headers or {})
        self.send_json_mode = True

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
        if req.json_mode and self.send_json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = dict(self.extra_headers)
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return self._post(f"{self.base_url}/chat/completions", headers, body)

    def _adapt(self, body: str) -> bool:
        low = body.lower()
        if self.token_param == "max_tokens" and "max_tokens" in low:
            self.token_param = "max_completion_tokens"
            return True
        if self.send_json_mode and ("response_format" in low or "json_object" in low):
            self.send_json_mode = False  # the pipeline validates and retries JSON itself
            return True
        return super()._adapt(body)
