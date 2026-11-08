"""LLM settings. Provider/model switch ONLY through env vars (or .env): zero code changes."""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _a(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    provider: str = Field("mock", validation_alias=_a("LLM_PROVIDER"))
    model: str = Field("", validation_alias=_a("LLM_MODEL"))
    temperature: float = Field(0.0, validation_alias=_a("LLM_TEMPERATURE"))
    timeout_s: float = Field(90.0, gt=0, validation_alias=_a("LLM_TIMEOUT_S"))
    max_concurrency: int = Field(2, ge=1, le=32, validation_alias=_a("LLM_MAX_CONCURRENCY"))
    max_retries: int = Field(4, ge=0, le=10, validation_alias=_a("LLM_MAX_RETRIES"))
    rpm: int = Field(10, ge=0, validation_alias=_a("LLM_RPM"), description="requests/minute, 0=off")
    rpd: int = Field(200, ge=0, validation_alias=_a("LLM_RPD"), description="requests/day, 0=off")
    budget_usd: float | None = Field(None, ge=0, validation_alias=_a("LLM_BUDGET_USD"))
    budget_calls: int | None = Field(None, ge=0, validation_alias=_a("LLM_BUDGET_CALLS"))
    cache_dir: str = Field(".cache/llm", validation_alias=_a("LLM_CACHE_DIR"))
    thinking_budget: int | None = Field(
        None, repr=False, validation_alias=_a("LLM_THINKING_BUDGET")
    )
    thinking_level: str | None = Field(None, repr=False, validation_alias=_a("LLM_THINKING_LEVEL"))

    gemini_api_key: str | None = Field(
        None, repr=False, validation_alias=_a("GEMINI_API_KEY", "GOOGLE_API_KEY")
    )
    groq_api_key: str | None = Field(None, repr=False, validation_alias=_a("GROQ_API_KEY"))
    anthropic_api_key: str | None = Field(
        None, repr=False, validation_alias=_a("ANTHROPIC_API_KEY")
    )
    openai_api_key: str | None = Field(None, repr=False, validation_alias=_a("OPENAI_API_KEY"))
    ollama_base_url: str = Field("http://localhost:11434", validation_alias=_a("OLLAMA_BASE_URL"))
    # provider "openai_compatible": any OpenAI-style /chat/completions endpoint
    base_url: str = Field("", validation_alias=_a("LLM_BASE_URL"))
    api_key: str | None = Field(None, repr=False, validation_alias=_a("LLM_API_KEY"))
    extra_headers: str = Field(
        "", repr=False, validation_alias=_a("LLM_EXTRA_HEADERS")
    )  # JSON object


DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-4.1-mini",
    "ollama": "gemma3:1b",
    "mock": "mock-1",
    "none": "rules-only",
}
