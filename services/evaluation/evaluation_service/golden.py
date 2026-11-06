"""Golden set: editor-labelled promos -> precision@k and best timestamp IoU per golden promo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from promo_common.timecode import TimecodeError, parse_timecode
from pydantic import BaseModel, ValidationError

from evaluation_service.matching import promo_iou


class GSeg(BaseModel):
    episode: int
    start_s: float
    end_s: float


class GoldenPromo(BaseModel):
    segments: list[GSeg]
    note: str = ""


def _t(v: Any) -> float:
    if isinstance(v, int | float):
        return float(v)
    return parse_timecode(str(v))


def load_golden(path: Path) -> list[GoldenPromo]:
    """Accepts ``{episode, start, end, note}`` or ``{segments: [...], note}``; times are
    ``mm:ss.xx`` strings or seconds."""
    data = yaml.safe_load(path.read_text()) or {}
    out: list[GoldenPromo] = []
    for i, row in enumerate(data.get("promos") or []):
        try:
            raw = row.get("segments") or [row]
            segs = [
                GSeg(episode=int(s["episode"]), start_s=_t(s["start"]), end_s=_t(s["end"]))
                for s in raw
            ]
            out.append(GoldenPromo(segments=segs, note=str(row.get("note", ""))))
        except (KeyError, ValueError, TypeError, TimecodeError, ValidationError) as e:
            raise ValueError(f"{path.name}: promo #{i + 1} is malformed: {e}") from e
    return out


@dataclass
class GoldenMatch:
    note: str
    best_iou: float
    best_rank: int | None
    found_at_k: bool


@dataclass
class GoldenReport:
    n_golden: int
    k: int
    iou_threshold: float
    precision_at_k: float
    recall_at_k: float
    matches: list[GoldenMatch]


def evaluate_golden(
    golden: list[GoldenPromo], promos: list[Any], k: int = 10, thr: float = 0.5
) -> GoldenReport:
    """``promos`` are ranked Promo objects (need ``.segments`` with episode/start_s/end_s)."""
    top = promos[:k]
    hits = sum(1 for p in top if any(promo_iou(p.segments, g.segments) >= thr for g in golden))
    matches = []
    for g in golden:
        best, rank = 0.0, None
        for p in promos:
            v = promo_iou(p.segments, g.segments)
            if v > best:
                best, rank = v, p.rank
        matches.append(
            GoldenMatch(g.note, round(best, 3), rank, bool(rank and rank <= k and best >= thr))
        )
    return GoldenReport(
        len(golden), k, thr,
        round(hits / max(1, len(top)), 3),
        round(sum(m.found_at_k for m in matches) / max(1, len(golden)), 3),
        matches,
    )  # fmt: skip
