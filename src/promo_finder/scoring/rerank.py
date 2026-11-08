"""One cross-show listwise rerank of the shortlist so scores are comparable across episodes."""

from __future__ import annotations

import logging
import threading
from typing import Any

from promo_finder.infrastructure.llm import (
    InvalidLLMOutput,
    LLMError,
    LLMRequest,
    ManagedLLM,
    load_prompt,
)
from promo_finder.infrastructure.llm.cache import make_key
from promo_finder.infrastructure.llm.errors import BudgetExceeded, DailyQuotaExhausted
from promo_finder.infrastructure.llm.json_utils import extract_json
from promo_finder.scoring.config import ScoringConfig
from promo_finder.scoring.service import _stable_order

log = logging.getLogger(__name__)
_lock = threading.Lock()


def _one_pass(
    llm: ManagedLLM, cfg: ScoringConfig, order: list[str], digests: dict[str, str],
    prior: dict[str, float], ns: str, bypass: bool,
) -> list[str] | None:  # fmt: skip
    prompt = load_prompt(cfg.rerank_prompt)
    labels = {cid: f"r{i + 1}" for i, cid in enumerate(order)}
    inv = {v: k for k, v in labels.items()}
    body = "\n\n".join(f"{labels[c]}: {digests[c]}" for c in order)
    key = make_key("rerank", prompt.sha, llm.model, str(llm.temperature), ns, body)
    if not bypass:
        hit = llm.cache.get(key)
        if hit:
            llm.note_cache_hit("rerank")
            return [inv[x] for x in hit["order"] if x in inv]
    note = ""
    for _ in range(cfg.max_validation_retries + 1):
        system, user = prompt.render(candidates=body, retry_note=note)
        meta: dict[str, Any] = {
            "labels": list(labels.values()),
            "prior": {labels[c]: prior.get(c, 0.0) for c in order},
        }
        try:
            resp = llm.complete(
                LLMRequest(system, user, "rerank", 12 * len(order) + 100, meta=meta)
            )
            data = extract_json(resp.text)
            got = [str(x) for x in data["order"]]
        except (DailyQuotaExhausted, BudgetExceeded):
            raise
        except (LLMError, InvalidLLMOutput, KeyError, TypeError):
            note = '\nYour reply was not valid. Return {"order": [...]} with every id exactly once.'
            continue
        if sorted(got) == sorted(labels.values()):
            llm.cache.set(key, {"order": got})
            return [inv[x] for x in got]
        note = "\nYour order must contain EVERY id exactly once and no others."
    return None


def rerank(
    llm: ManagedLLM, cfg: ScoringConfig, digests: dict[str, str], prior: dict[str, float],
    ns: str = "", bypass: bool = False,
) -> dict[str, float] | None:  # fmt: skip
    """Return average rank (1 = best) per candidate id, or None if every pass failed."""
    base = _stable_order(digests, "rerank")
    passes = [base, list(reversed(base))][: max(1, cfg.rerank_passes)]
    ranks: dict[str, list[int]] = {cid: [] for cid in digests}
    for order in passes:
        result = _one_pass(llm, cfg, order, digests, prior, ns, bypass)
        if result is None:
            log.warning("rerank_pass_failed")
            continue
        for pos, cid in enumerate(result, start=1):
            ranks[cid].append(pos)
    if not any(ranks.values()):
        return None
    return {cid: sum(r) / len(r) for cid, r in ranks.items() if r}
