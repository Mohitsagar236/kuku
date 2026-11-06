"""Provider-agnostic LLM interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMRequest:
    system: str
    user: str
    task: str = "generic"
    max_tokens: int = 1024
    temperature: float = 0.0
    json_mode: bool = True
    # Structured description of what is being asked. Real providers ignore it; the mock
    # provider uses it to answer deterministically without parsing prose.
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    model: str
    provider: str
    raw: dict[str, Any] | None = None
    truncated: bool = False  # provider says the answer hit the output-token limit


class LLMClient(ABC):
    """One adapter per provider. Stateless apart from credentials."""

    provider: str = "base"
    model: str = ""

    @abstractmethod
    def complete(self, req: LLMRequest) -> LLMResponse: ...
