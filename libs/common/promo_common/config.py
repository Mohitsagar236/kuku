"""12-factor settings. All values come from env vars / .env, never from code."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PF_", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    log_format: str = "json"  # json | text
    database_url: str = "sqlite:///./promo_finder.sqlite"
    redis_url: str | None = None  # None => in-memory cache / in-process runner
    max_episode_file_bytes: int = 2_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()


def find_config_dir() -> Path:
    """Locate ``config/`` (PF_CONFIG_DIR, else walk up from cwd and from this file)."""
    env = os.environ.get("PF_CONFIG_DIR")
    if env:
        return Path(env)
    for base in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if (base / "config" / "pipeline.yaml").is_file():
            return base / "config"
    raise FileNotFoundError("config/pipeline.yaml not found; set PF_CONFIG_DIR")


def find_prompts_dir() -> Path:
    env = os.environ.get("PF_PROMPTS_DIR")
    if env:
        return Path(env)
    return find_config_dir().parent / "prompts"
