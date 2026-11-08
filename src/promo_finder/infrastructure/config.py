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
    max_episode_file_bytes: int = 2_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()


def find_config_dir() -> Path:
    """Explicit override, otherwise versioned defaults shipped in the wheel."""
    path = (
        Path(os.environ["PF_CONFIG_DIR"])
        if os.environ.get("PF_CONFIG_DIR")
        else Path(__file__).resolve().parents[1] / "resources" / "config"
    )
    if not (path / "pipeline.yaml").is_file():
        raise FileNotFoundError(f"pipeline.yaml not found in {path}")
    return path


def find_prompts_dir() -> Path:
    if os.environ.get("PF_PROMPTS_DIR"):
        return Path(os.environ["PF_PROMPTS_DIR"])
    if os.environ.get("PF_CONFIG_DIR"):
        return find_config_dir().parent / "prompts"
    return Path(__file__).resolve().parents[1] / "resources" / "prompts"
