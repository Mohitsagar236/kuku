"""Combine rule and LLM scores into one final score. Pure functions, unit-tested."""

from __future__ import annotations

from dataclasses import dataclass, field

from promo_common.schemas import Candidate, LLMScores

from ranking_service.config import RankingConfig


@dataclass
class Fused:
    cand: Candidate
    llm: LLMScores | None
    dims: dict[str, float]
    final: float
    confidence: float
    flags: list[str] = field(default_factory=list)
    blended: float = 0.0  # after cross-show rerank (== final when not reranked)
    rerank_rank: float | None = None


def _mix(rule: float, llm: float | None, share: float) -> float:
    return rule if llm is None else share * llm + (1 - share) * rule


def fuse(cand: Candidate, llm: LLMScores | None, cfg: RankingConfig) -> Fused:
    f, sig = cfg.fusion, cand.signals
    flags: list[str] = []
    main = llm is not None and llm.main_ok
    clar = llm is not None and llm.clarity_ok and llm.clarity is not None
    if not main:
        flags.append("rule_fallback_main")
    if not clar:
        flags.append("rule_fallback_clarity")
    hook = _mix(sig.hook, llm.hook if main and llm else None, f.llm_share)
    cliff = _mix(sig.cliffhanger, llm.cliffhanger if main and llm else None, f.llm_share)
    inten = _mix(sig.intensity, llm.intensity if main and llm else None, f.llm_share)
    clarity = _mix(sig.clarity, llm.clarity if clar and llm else None, f.clarity_llm_share)
    w = f.weights
    final = w.hook * hook + w.cliffhanger * cliff + w.clarity * clarity + w.intensity * inten
    if main and llm and llm.spoiler_risk is not None:
        final -= f.spoiler_penalty_per_point * llm.spoiler_risk

    conf = cfg.confidence.no_llm
    if main and llm:
        diffs = [
            abs((llm.hook or 0) - sig.hook),
            abs((llm.cliffhanger or 0) - sig.cliffhanger),
            abs((llm.intensity or 0) - sig.intensity),
        ]
        agree = 1 - (sum(diffs) / len(diffs)) / 10
        conf = cfg.confidence.base_with_llm + cfg.confidence.agreement_gain * max(0.0, agree)
    if not clar:
        conf -= 0.10
    if cand.kind == "stitched":
        if llm is not None and llm.coherence_ok and llm.coherence is not None:
            mult = f.stitched_base + 0.03 * llm.coherence
            conf *= 0.6 + 0.04 * llm.coherence
        else:
            mult = f.stitched_unknown
            conf *= 0.6
            flags.append("coherence_unchecked")
        final *= mult
    if sig.continuity_dependent:
        conf -= 0.10
    if sig.opens_with_recap:
        conf -= 0.05
    final = max(0.0, min(10.0, final))
    conf = max(0.05, min(0.95, conf))
    dims = {"hook": hook, "cliffhanger": cliff, "clarity": clarity, "intensity": inten}
    return Fused(cand, llm, dims, round(final, 3), round(conf, 3), flags, blended=round(final, 3))
