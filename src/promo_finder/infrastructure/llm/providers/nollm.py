"""LLM_PROVIDER=none: rules-only mode. Any call stops scoring; the pipeline falls back to rules."""

from __future__ import annotations

from promo_finder.infrastructure.llm.base import LLMClient, LLMRequest, LLMResponse
from promo_finder.infrastructure.llm.errors import DailyQuotaExhausted


class NoLLM(LLMClient):
    provider = "none"
    model = "rules-only"

    def complete(self, req: LLMRequest) -> LLMResponse:
        raise DailyQuotaExhausted("LLM disabled (LLM_PROVIDER=none): rules-only run")
