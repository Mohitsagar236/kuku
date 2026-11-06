"""Per-job budget cap (USD and/or number of calls) plus pricing lookup."""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

from promo_common.llm.errors import BudgetExceeded


class Pricing:
    """USD per 1M tokens, from config/pricing.yaml. Unknown models cost 0 and are flagged."""

    def __init__(self, table: dict[str, dict[str, float]] | None = None) -> None:
        self.table = table or {}

    @classmethod
    def load(cls, path: Path) -> Pricing:
        if not path.is_file():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls({str(k): dict(v) for k, v in (data.get("models") or {}).items()})

    def known(self, model: str) -> bool:
        return model in self.table

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        p = self.table.get(model)
        if not p:
            return 0.0
        return (tokens_in * p.get("in", 0.0) + tokens_out * p.get("out", 0.0)) / 1e6


class Budget:
    def __init__(self, max_usd: float | None = None, max_calls: int | None = None) -> None:
        self.max_usd, self.max_calls = max_usd, max_calls
        self.spent_usd = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def ensure(self) -> None:
        with self._lock:
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise BudgetExceeded(f"call cap {self.max_calls} reached")
            if self.max_usd is not None and self.spent_usd >= self.max_usd:
                raise BudgetExceeded(f"spend cap ${self.max_usd:.2f} reached")

    def add(self, usd: float) -> None:
        with self._lock:
            self.calls += 1
            self.spent_usd += usd
