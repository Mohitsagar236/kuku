"""Detect episode openings that replay the end of the previous episode."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from promo_common.schemas import BeatType, Episode

from candidate_service.config import CandidateConfig

_NON = re.compile(r"[^a-z0-9' ]+")


def normalize_line(text: str) -> str:
    return " ".join(_NON.sub(" ", text.lower()).split())


def detect_recap(prev: Episode | None, ep: Episode, cfg: CandidateConfig) -> dict[str, str]:
    """Map beat ids in ``ep``'s opening to the normalized text of the matching beat in ``prev``.

    Matching is fuzzy (SequenceMatcher ratio), dialogue and action only, min N words.
    """
    if prev is None:
        return {}
    kinds = (BeatType.DIALOGUE, BeatType.ACTION)
    prev_end = max((b.end_s for b in prev.beats), default=0.0)
    tail = [
        normalize_line(b.text)
        for b in prev.beats
        if b.type in kinds and b.end_s >= prev_end - cfg.recap_tail_s
    ]
    tail = [t for t in tail if len(t.split()) >= cfg.recap_min_words]
    out: dict[str, str] = {}
    for b in ep.beats:
        if b.start_s > cfg.recap_head_s:
            break
        if b.type not in kinds:
            continue
        norm = normalize_line(b.text)
        if len(norm.split()) < cfg.recap_min_words:
            continue
        best: tuple[float, str] | None = None
        words = set(norm.split())
        for t in tail:
            sm = SequenceMatcher(None, norm, t)
            if (
                sm.real_quick_ratio() < cfg.recap_similarity
                or sm.quick_ratio() < cfg.recap_similarity
            ):
                continue
            r = sm.ratio()
            tw = set(t.split())
            jaccard = len(words & tw) / len(words | tw)
            # both character-level and word-level similarity: templated lines that differ in
            # one key word must not be treated as a replay
            if r >= cfg.recap_similarity and jaccard >= 0.75 and (best is None or r > best[0]):
                best = (r, t)
        if best:
            out[b.id] = best[1]
    return out
