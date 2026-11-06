"""Deterministic offline provider for tests and dry runs.

It answers from ``LLMRequest.meta`` (structured), never by parsing prose, and returns the same
JSON shapes the real prompts ask for. Optional ``faults`` simulate failures, one per call.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from promo_common.llm.base import LLMClient, LLMRequest, LLMResponse
from promo_common.llm.errors import (
    DailyQuotaExhausted,
    LLMRateLimitError,
    LLMTransientError,
)


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _clip(x: float) -> int:
    return max(0, min(10, round(x)))


class MockProvider(LLMClient):
    provider = "mock"

    def __init__(
        self, model: str = "mock-1", faults: list[str] | None = None, jitter: int = 0
    ) -> None:
        self.model = model
        self.faults = list(faults or [])
        self.jitter = jitter  # >0: scores move between repeated calls (stability tests)
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, req: LLMRequest) -> LLMResponse:
        with self._lock:
            self.calls += 1
            n = self.calls
            fault = self.faults.pop(0) if self.faults else None
        if fault == "rate_limit":
            raise LLMRateLimitError("mock 429", retry_after=0.0)
        if fault == "transient":
            raise LLMTransientError("mock 503")
        if fault == "daily_quota":
            raise DailyQuotaExhausted("mock daily quota")
        noise = (n * 7 % 3 - 1) * self.jitter if self.jitter else 0
        body = {
            "score": self._score,
            "clarity": self._clarity,
            "coherence": self._coherence,
            "rerank": self._rerank,
        }.get(req.task, lambda m, nz, f: {})(req.meta, noise, fault)
        text = "not json at all" if fault == "invalid_json" else json.dumps(body)
        if fault == "truncated":
            text = text[: len(text) // 2]
        return LLMResponse(
            text=text,
            tokens_in=(len(req.system) + len(req.user)) // 4,
            tokens_out=len(text) // 4,
            latency_ms=1.0,
            model=self.model,
            provider="mock",
            truncated=fault == "truncated",
        )

    # -- tasks ----------------------------------------------------------------------------
    def _score(self, meta: dict[str, Any], noise: int, fault: str | None) -> dict[str, Any]:
        items = []
        for c in meta["candidates"]:
            texts: dict[str, str] = c["texts"]
            hook = c["hook_ok"][0]
            cliff = c["cliff_ok"][-1]
            if fault == "unknown_id":
                hook = "e99_b999"
            elif fault == "late_hook":
                hook = c["beats"][-1]
            body = " ".join(texts[b] for b in c["beats"])
            bang = body.count("!") + body.count("?")
            seed = _h(c["label"], hook, cliff)
            item = {
                "id": c["label"],
                "hook": hook,
                "cliff": cliff,
                "h": _clip(3 + texts.get(hook, "").count("!") * 2 + seed % 3 + noise),
                "c": _clip(3 + texts.get(cliff, "").count("?") * 2 + (seed >> 3) % 3 + noise),
                "i": _clip(2 + bang / 3 + (seed >> 6) % 3 + noise),
                "s": _clip((seed >> 9) % 4),
                "why": "Mock rationale: strong opening line and an unresolved ending.",
            }
            if c.get("trim_end"):
                item["te"] = c["trim_end"]
            items.append(item)
        return {"results": items}

    def _clarity(self, meta: dict[str, Any], noise: int, fault: str | None) -> dict[str, Any]:
        items = [
            {
                "id": c["label"],
                "clarity": _clip(4 + _h(c["text"]) % 5 + noise),
                "gap": "Unclear who the characters are." if _h(c["text"]) % 2 else "",
            }
            for c in meta["candidates"]
        ]
        return {"results": items}

    def _coherence(self, meta: dict[str, Any], noise: int, fault: str | None) -> dict[str, Any]:
        items = [
            {"id": c["label"], "coherence": _clip(4 + _h(c["text"], "coh") % 5 + noise), "gap": ""}
            for c in meta["candidates"]
        ]
        return {"results": items}

    def _rerank(self, meta: dict[str, Any], noise: int, fault: str | None) -> dict[str, Any]:
        ids: list[str] = list(meta["labels"])
        prior: dict[str, float] = meta.get("prior", {})
        order = sorted(
            ids, key=lambda i: (-prior.get(i, 0.0) - (_h(i) % 5) * 0.01 * self.jitter, i)
        )
        if fault == "bad_permutation":
            order = order[:-1]
        return {"order": order}
