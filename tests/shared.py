from __future__ import annotations

from promo_finder.infrastructure.llm import LLMRequest, LLMResponse
from promo_finder.infrastructure.llm.cache import MemoryCache
from promo_finder.infrastructure.llm.managed import ManagedLLM
from promo_finder.infrastructure.llm.providers.mock import MockProvider
from tests.candidate.conftest import D, line_text, make_episode


class SpyMock(MockProvider):
    """Mock that records every request it receives."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.requests: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        return super().complete(req)


def story_episode(n: int, logline: str, notes: str):
    specs = []
    for i in range(30):
        text = line_text(i, f"ep{n}", 13 + 4 * (n - 1))
        if i % 5 == 0:
            text += " you lied to me!"
        specs.append((D, i * 4.0, i * 4.0 + 2.5, text, "ANA" if i % 2 else "BEN"))
    return make_episode(n, specs, notes=notes, logline=logline)


def make_llm(provider: MockProvider | None = None, cache: MemoryCache | None = None) -> ManagedLLM:
    return ManagedLLM(provider or SpyMock(), cache=cache or MemoryCache(), sleep=lambda s: None)
