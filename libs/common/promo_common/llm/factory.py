"""Build a ManagedLLM from env-driven settings. The only place that knows provider names."""

from __future__ import annotations

from pathlib import Path

from promo_common.config import find_config_dir
from promo_common.llm.base import LLMClient
from promo_common.llm.budget import Budget, Pricing
from promo_common.llm.cache import FileCache, MemoryCache
from promo_common.llm.errors import LLMAuthError
from promo_common.llm.managed import ManagedLLM
from promo_common.llm.providers.anthropic import AnthropicProvider
from promo_common.llm.providers.gemini import GeminiProvider
from promo_common.llm.providers.mock import MockProvider
from promo_common.llm.providers.nollm import NoLLM
from promo_common.llm.providers.ollama import OllamaProvider
from promo_common.llm.providers.openai_compat import GroqProvider, OpenAICompatProvider
from promo_common.llm.ratelimit import RateLimiter
from promo_common.llm.settings import DEFAULT_MODELS, LLMSettings

PROVIDERS = ("anthropic", "openai", "gemini", "groq", "ollama", "mock", "none")


def build_client(s: LLMSettings) -> LLMClient:
    name = s.provider.lower()
    model = s.model or DEFAULT_MODELS.get(name, "")
    if name == "mock":
        return MockProvider(model)
    if name == "none":
        return NoLLM()
    if name == "ollama":
        return OllamaProvider(model, None, s.timeout_s, s.ollama_base_url)
    keys = {
        "gemini": s.gemini_api_key,
        "groq": s.groq_api_key,
        "anthropic": s.anthropic_api_key,
        "openai": s.openai_api_key,
    }
    if name not in keys:
        raise ValueError(f"unknown LLM_PROVIDER {s.provider!r}; choose one of {PROVIDERS}")
    key = keys[name]
    if not key:
        env = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
               "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[name]  # fmt: skip
        raise LLMAuthError(f"LLM_PROVIDER={name} but {env} is not set (put it in .env)")
    if name == "gemini":
        g = GeminiProvider(model, key, s.timeout_s)
        # Gemini 2.5 "thinks" by default and those tokens count against the output limit, which
        # can truncate JSON. Disable for non-Pro models unless the user sets LLM_THINKING_BUDGET.
        g.thinking_budget = (
            s.thinking_budget if s.thinking_budget is not None else (None if "pro" in model else 0)
        )
        return g
    if name == "groq":
        return GroqProvider(model, key, s.timeout_s)
    if name == "anthropic":
        return AnthropicProvider(model, key, s.timeout_s)
    return OpenAICompatProvider(model, key, s.timeout_s)


def build_llm(
    s: LLMSettings | None = None, raw_dir: Path | None = None, persist: bool = True
) -> ManagedLLM:
    s = s or LLMSettings()
    client = build_client(s)
    cache_root = Path(s.cache_dir)
    cache = FileCache(cache_root / "responses") if persist else MemoryCache()
    quota_file = cache_root / f"quota_{client.provider}_{client.model}".replace(":", "_")
    limiter = RateLimiter(
        s.rpm if client.provider != "mock" else 0,
        s.rpd if client.provider != "mock" else 0,
        state_path=quota_file.with_suffix(".json")
        if persist and client.provider != "mock"
        else None,
    )
    try:
        pricing = Pricing.load(find_config_dir() / "pricing.yaml")
    except FileNotFoundError:
        pricing = Pricing()
    return ManagedLLM(
        client,
        cache=cache,
        limiter=limiter,
        budget=Budget(s.budget_usd, s.budget_calls),
        pricing=pricing,
        raw_dir=raw_dir,
        max_retries=s.max_retries,
        temperature=s.temperature,
    )
