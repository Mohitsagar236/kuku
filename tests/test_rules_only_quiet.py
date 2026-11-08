"""Rules-only mode must not look like, or leave behind, an exhausted quota."""

import pytest

from promo_finder.infrastructure.llm import ManagedLLM
from promo_finder.infrastructure.llm.base import LLMRequest
from promo_finder.infrastructure.llm.errors import LLMDisabled
from promo_finder.infrastructure.llm.providers.nollm import NoLLM
from promo_finder.infrastructure.llm.ratelimit import RateLimiter


def test_none_provider_raises_llm_disabled_and_keeps_limiter_untouched(tmp_path):
    limiter = RateLimiter(rpm=0, rpd=200, state_path=tmp_path / "q.json")
    llm = ManagedLLM(NoLLM(), limiter=limiter)
    with pytest.raises(LLMDisabled):
        llm.complete(LLMRequest(task="t", system="s", user="u"))
    assert limiter.used_today == 0
    assert not (tmp_path / "q.json").exists()
