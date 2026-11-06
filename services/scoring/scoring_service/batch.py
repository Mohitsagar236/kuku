"""Run one LLM batch with per-candidate validation and targeted retry.

Valid items are kept; only rejected candidates are re-asked (with the reason), so a single bad id
does not re-bill the whole batch. Quota/budget errors propagate; other LLM errors fail the batch.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from promo_common.llm import (
    BudgetExceeded,
    DailyQuotaExhausted,
    InvalidLLMOutput,
    LLMError,
    LLMRequest,
    ManagedLLM,
)
from promo_common.llm.json_utils import extract_json

from scoring_service.ids import CandView

log = logging.getLogger(__name__)


@dataclass
class Job:
    cand_id: str
    view: CandView
    key: str


Render = Callable[[list[Job], dict[str, str], str], LLMRequest]
Validate = Callable[[dict[str, Any], Job], tuple[dict[str, Any] | None, str | None]]


def run_batch(
    llm: ManagedLLM,
    jobs: list[Job],
    render: Render,
    validate: Validate,
    retries: int,
    bump: Callable[[str], None] | None = None,
    task: str = "",
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    labels = {j.cand_id: f"c{i + 1}" for i, j in enumerate(jobs)}
    pending = list(jobs)
    ok: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    note = ""
    bump = bump or (lambda key: None)
    for attempt in range(retries + 1):
        if attempt:
            bump(f"{task}_retry_calls")
        req = render(pending, labels, note)
        try:
            resp = llm.complete(req)
        except (DailyQuotaExhausted, BudgetExceeded):
            raise
        except LLMError as e:
            return ok, {**errors, **{j.cand_id: f"llm_error: {e}" for j in pending}}
        try:
            parsed = extract_json(resp.text)
            rows = parsed["results"] if isinstance(parsed, dict) else parsed
            by_label = {str(r.get("id")).strip().lower(): r for r in rows if isinstance(r, dict)}
        except (InvalidLLMOutput, KeyError, TypeError, AttributeError) as e:
            bump(f"{task}_invalid_json")
            for j in pending:
                errors[j.cand_id] = f"invalid_json: {e}"
            note = "\nYour previous reply was not valid JSON in the required shape. JSON only."
            continue
        failed: list[Job] = []
        reasons: list[str] = []
        for j in pending:
            row = by_label.get(labels[j.cand_id].lower())
            if row is None:
                result, err = None, "missing from response"
            else:
                result, err = validate(row, j)
            if result is not None:
                ok[j.cand_id] = result
                errors.pop(j.cand_id, None)
            else:
                failed.append(j)
                bump(f"{task}_rejected")
                errors[j.cand_id] = err or "rejected"
                reasons.append(f"{labels[j.cand_id]}: {err}")
        pending = failed
        if not pending:
            break
        note = (
            "\nYour previous answer was REJECTED for: "
            + "; ".join(reasons)
            + ". Answer again ONLY for the candidates listed above, obeying the hard rules."
        )
        log.warning(
            "llm_validation_retry", extra={"rejected": len(pending), "attempt": attempt + 1}
        )
    return ok, errors
