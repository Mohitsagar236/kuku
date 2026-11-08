"""A mock (or any non-Gemini) run must never share cache entries with real Gemini judgments."""

from __future__ import annotations

from promo_finder.infrastructure.llm.cache import make_key
from promo_finder.scoring.service import Scorer
from tests.shared import make_llm


def _scorer(show, scfg, provider: str):
    llm = make_llm()
    llm.client.model = "same-model-name"
    llm.client.provider = provider
    return Scorer(llm, show, scfg)


def test_provider_is_part_of_the_cache_key_except_for_legacy_gemini(show, scfg):
    gem = _scorer(show, scfg, "gemini")._key("score", "sha", "blob")
    mock = _scorer(show, scfg, "mock")._key("score", "sha", "blob")
    other = _scorer(show, scfg, "ollama")._key("score", "sha", "blob")
    assert len({gem, mock, other}) == 3
    # Gemini keys are exactly the pre-namespacing recipe, so retained runs still resume.
    s = _scorer(show, scfg, "gemini")
    assert gem == make_key("score", "sha", "same-model-name", str(s.llm.temperature), s.ns, "blob")
