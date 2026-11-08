"""Build a ManagedLLM from env-driven settings. The only place that knows provider names."""

from __future__ import annotations

import json
from pathlib import Path

from promo_common.config import find_config_dir
from promo_common.llm.base import LLMClient
from promo_common.llm.budget import Budget, Pricing
from promo_common.llm.cache import FileCache, MemoryCache
from promo_common.llm.errors import LLMAuthError, LLMConfigError
from promo_common.llm.managed import ManagedLLM
from promo_common.llm.providers.anthropic import AnthropicProvider
from promo_common.llm.providers.gemini import GeminiProvider
from promo_common.llm.providers.mock import MockProvider
from promo_common.llm.providers.nollm import NoLLM
from promo_common.llm.providers.ollama import OllamaProvider
from promo_common.llm.providers.openai_compat import (
    GroqProvider,
    OpenAICompatibleGateway,
    OpenAICompatProvider,
)
from promo_common.llm.ratelimit import RateLimiter
from promo_common.llm.settings import DEFAULT_MODELS, LLMSettings

PROVIDERS = ("anthropic", "openai", "openai_compatible", "gemini", "groq", "ollama", "mock", "none")


def parse_extra_headers(raw: str) -> dict[str, str]:
    """LLM_EXTRA_HEADERS: a JSON object of string header values ('' = none)."""
    if not raw.strip():
        return {}
    try:
        val = json.loads(raw)
    except ValueError as e:
        raise LLMConfigError(
            'LLM_EXTRA_HEADERS must be a JSON object, e.g. {"X-Org": "kuku"}'
        ) from e
    if not isinstance(val, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in val.items()
    ):
        raise LLMConfigError("LLM_EXTRA_HEADERS must map header names to string values")
    return val


def _gateway(s: LLMSettings) -> OpenAICompatibleGateway:
    if not s.base_url:
        raise LLMConfigError(
            "LLM_PROVIDER=openai_compatible needs LLM_BASE_URL (e.g. https://host/v1) in .env"
        )
    if not s.model:
        raise LLMConfigError(
            "LLM_PROVIDER=openai_compatible needs LLM_MODEL (the model id to send)"
        )
    return OpenAICompatibleGateway(
        s.model, s.api_key or None, s.base_url, parse_extra_headers(s.extra_headers), s.timeout_s
    )


def build_client(s: LLMSettings) -> LLMClient:
    name = s.provider.lower()
    model = s.model or DEFAULT_MODELS.get(name, "")
    if name == "mock":
        return MockProvider(model)
    if name == "none":
        return NoLLM()
    if name == "ollama":
        return OllamaProvider(model, None, s.timeout_s, s.ollama_base_url)
    if name == "openai_compatible":
        return _gateway(s)
    keys = {
        "gemini": s.gemini_api_key,
        "groq": s.groq_api_key,
        "anthropic": s.anthropic_api_key,
        "openai": s.openai_api_key,
    }
    if name not in keys:
        raise LLMConfigError(f"unknown LLM_PROVIDER {s.provider!r}; choose one of {PROVIDERS}")
    key = keys[name]
    if not key:
        env = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
               "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[name]  # fmt: skip
        raise LLMAuthError(f"LLM_PROVIDER={name} but {env} is not set (put it in .env)")
    if name == "gemini":
        g = GeminiProvider(model, key, s.timeout_s)
        # Gemini 2.5 Flash "thinks" by default and those tokens count against the output limit,
        # which can truncate JSON: switch it off there. Other models (3.x) either do not think by
        # default or use a different control, so nothing is sent unless LLM_THINKING_BUDGET is set.
        low = model.lower()
        if s.thinking_budget is not None:
            g.thinking_budget = s.thinking_budget
        elif s.thinking_level:
            g.thinking_level = s.thinking_level
        elif "2.5-flash" in low:
            g.thinking_budget = 0  # 2.5 control
        elif "flash" in low and "pro" not in low:
            g.thinking_level = "minimal"  # 3.x control; the adapter drops it if a model rejects it
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
    safe = f"{client.provider}_{client.model}".replace(":", "_").replace("/", "_")
    quota_file = cache_root / f"quota_{safe}.json"  # NOT with_suffix: model names contain dots
    limiter = RateLimiter(
        s.rpm if client.provider != "mock" else 0,
        s.rpd if client.provider != "mock" else 0,
        state_path=quota_file if persist and client.provider != "mock" else None,
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
