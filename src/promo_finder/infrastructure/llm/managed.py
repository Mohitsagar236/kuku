"""ManagedLLM: wraps a provider with rate limits, budget, retries/backoff, logging, raw capture.

Validation-level retries (bad JSON, unknown beat ids) live in the scoring service; this layer
only handles transport-level concerns.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promo_finder.infrastructure.files import atomic_write_text
from promo_finder.infrastructure.llm.base import LLMClient, LLMRequest, LLMResponse
from promo_finder.infrastructure.llm.budget import Budget, Pricing
from promo_finder.infrastructure.llm.cache import Cache, MemoryCache
from promo_finder.infrastructure.llm.errors import DailyQuotaExhausted, LLMTransientError
from promo_finder.infrastructure.llm.ratelimit import RateLimiter

log = logging.getLogger(__name__)


@dataclass
class CallRecord:
    seq: int
    task: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: float
    attempts: int
    truncated: bool = False
    phase: str = ""


class ManagedLLM:
    def __init__(
        self,
        client: LLMClient,
        *,
        cache: Cache | None = None,
        limiter: RateLimiter | None = None,
        budget: Budget | None = None,
        pricing: Pricing | None = None,
        raw_dir: Path | None = None,
        max_retries: int = 4,
        backoff_base_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        temperature: float = 0.0,
    ) -> None:
        self.client = client
        self.cache: Cache = cache or MemoryCache()
        self.limiter = limiter or RateLimiter()
        self.budget = budget or Budget()
        self.pricing = pricing or Pricing()
        self.raw_dir = raw_dir
        self.max_retries, self.backoff_base_s, self._sleep = max_retries, backoff_base_s, sleep
        self.temperature = temperature
        self.phase = ""  # label stamped on every call (main / ablation / stability ...)
        self.records: list[CallRecord] = []
        self.retry_events: list[dict[str, Any]] = []
        self.cache_hits: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self._rng = random.Random(0)

    @property
    def provider(self) -> str:
        return self.client.provider

    @property
    def model(self) -> str:
        return self.client.model

    def note_cache_hit(self, task: str, n: int = 1) -> None:
        with self._lock:
            self.cache_hits[task] += n

    def complete(self, req: LLMRequest) -> LLMResponse:
        with self.budget.reserve():
            return self._complete(req)

    def _complete(self, req: LLMRequest) -> LLMResponse:
        req.temperature = self.temperature
        attempt = 0
        while True:
            self.limiter.acquire()
            try:
                resp = self.client.complete(req)
                break
            except DailyQuotaExhausted:
                self.limiter.mark_exhausted()
                raise
            except LLMTransientError as e:
                attempt += 1
                kind = getattr(e, "kind", "transient")
                gave_up = attempt > self.max_retries
                with self._lock:
                    self.retry_events.append(
                        {"task": req.task, "phase": self.phase, "attempt": attempt,
                         "kind": kind, "gave_up": gave_up}
                    )  # fmt: skip
                if gave_up:
                    raise
                wait = getattr(e, "retry_after", None)
                if wait is None:
                    wait = min(60.0, self.backoff_base_s * 2 ** (attempt - 1))
                wait += self._rng.uniform(0, 0.25 * max(wait, 0.1))
                log.warning(
                    "llm_retry",
                    extra={
                        "task": req.task,
                        "attempt": attempt,
                        "wait_s": round(wait, 2),
                        "error_kind": kind,
                        "error": type(e).__name__,
                    },
                )
                self._sleep(wait)
        cost = self.pricing.cost(resp.model or self.model, resp.tokens_in, resp.tokens_out)
        if cost == 0.0:
            cost = self.pricing.cost(self.model, resp.tokens_in, resp.tokens_out)
        self.budget.add(cost)
        with self._lock:
            seq = len(self.records) + 1
            self.records.append(
                CallRecord(seq, req.task, resp.model, resp.tokens_in, resp.tokens_out,
                           resp.latency_ms, cost, attempt + 1, resp.truncated, self.phase)
            )  # fmt: skip
        log.info(
            "llm_call",
            extra={"task": req.task, "model": resp.model, "tokens_in": resp.tokens_in,
                   "tokens_out": resp.tokens_out, "latency_ms": round(resp.latency_ms, 1),
                   "cost_usd": round(cost, 6), "seq": seq},
        )  # fmt: skip
        if resp.truncated:
            log.warning("llm_truncated", extra={"task": req.task, "tokens_out": resp.tokens_out})
        self._save_raw(seq, req, resp)
        return resp

    def _save_raw(self, seq: int, req: LLMRequest, resp: LLMResponse) -> None:
        if not self.raw_dir:
            return
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = {
            "seq": seq, "task": req.task, "provider": resp.provider, "model": resp.model,
            "temperature": req.temperature, "tokens_in": resp.tokens_in,
            "tokens_out": resp.tokens_out, "latency_ms": round(resp.latency_ms, 1),
            "system": req.system, "user": req.user, "response": resp.text,
        }  # fmt: skip
        atomic_write_text(self.raw_dir / f"{seq:04d}_{req.task}.json", json.dumps(doc, indent=1))

    def summary(self) -> dict[str, Any]:
        by_task: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        )
        for r in self.records:
            t = by_task[r.task]
            t["calls"] += 1
            t["tokens_in"] += r.tokens_in
            t["tokens_out"] += r.tokens_out
            t["cost_usd"] += r.cost_usd
        by_phase: dict[str, dict[str, int]] = defaultdict(
            lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        )
        for r in self.records:
            p = by_phase[r.phase or "-"]
            p["calls"] += 1
            p["tokens_in"] += r.tokens_in
            p["tokens_out"] += r.tokens_out
        lat = sorted(r.latency_ms for r in self.records)
        by_kind: dict[str, int] = defaultdict(int)
        for ev in self.retry_events:
            by_kind[ev["kind"]] += 1
        return {
            "latency_ms": {
                "mean": round(sum(lat) / len(lat), 1) if lat else 0.0,
                "p50": round(lat[len(lat) // 2], 1) if lat else 0.0,
                "p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1) if lat else 0.0,
                "max": round(lat[-1], 1) if lat else 0.0,
            },
            "retries": {
                "count": len(self.retry_events),
                "by_kind": dict(by_kind),
                "events": list(self.retry_events),
            },
            "truncated_calls": sum(r.truncated for r in self.records),
            "by_phase": {k: dict(v) for k, v in by_phase.items()},
            "provider": self.provider,
            "model": self.model,
            "calls": len(self.records),
            "tokens_in": sum(r.tokens_in for r in self.records),
            "tokens_out": sum(r.tokens_out for r in self.records),
            "cost_usd": round(sum(r.cost_usd for r in self.records), 6),
            "price_known": self.pricing.known(self.model),
            "cache_hits": dict(self.cache_hits),
            "by_task": {k: dict(v) for k, v in by_task.items()},
        }
