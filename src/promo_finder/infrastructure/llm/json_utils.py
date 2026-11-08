"""Lenient-but-safe JSON extraction from model output."""

from __future__ import annotations

import json
import re
from typing import Any

from promo_finder.infrastructure.llm.errors import InvalidLLMOutput

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> Any:
    t = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1)
    end = max(t.rfind("}"), t.rfind("]"))
    if start < 0 or end <= start:
        raise InvalidLLMOutput("no JSON object in response")
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError as e:
        raise InvalidLLMOutput(f"invalid JSON: {e.msg}") from e
