"""Combine rule and LLM scores into one final score. Pure functions, unit-tested."""

from __future__ import annotations

from dataclasses import dataclass, field

from promo_finder.domain.models import Candidate, LLMScores
from promo_finder.ranking.config import RankingConfig


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
    llm_cliff = llm.ranking_cliffhanger if main and llm else None  # score of the delivered end
    cliff = _mix(sig.cliffhanger, llm_cliff, f.llm_share)
    inten = _mix(sig.intensity, llm.intensity if main and llm else None, f.llm_share)
    clarity = _mix(sig.clarity, llm.clarity if clar and llm else None, f.clarity_llm_share)
    w = f.weights
    final = w.hook * hook + w.cliffhanger * cliff + w.clarity * clarity + w.intensity * inten
    if main and llm and llm.spoiler_risk is not None:
        final -= f.spoiler_penalty_per_point * llm.spoiler_risk

    conf = cfg.confidence.no_llm
    if main and llm:
        diffs = [abs((llm.hook or 0) - sig.hook), abs((llm.intensity or 0) - sig.intensity)]
        if not llm.end_changed:  # rule cliffhanger describes the ORIGINAL end only
            diffs.append(abs((llm.cliffhanger or 0) - sig.cliffhanger))
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


def failed_gates(llm: LLMScores | None, cfg: RankingConfig) -> list[str]:
    """Which hard gates (if any) this candidate fails. Only LLM scores are gated."""
    if llm is None or not llm.main_ok:
        return []
    out = []
    if (llm.hook or 0) < cfg.gates.min_hook:
        out.append("hook")
    cliff = llm.ranking_cliffhanger
    if cliff is not None and cliff < cfg.gates.min_cliffhanger:
        out.append("cliffhanger")
    if llm.clarity_ok and llm.clarity is not None and llm.clarity < cfg.gates.min_clarity:
        out.append("clarity")
    return out


def attribution(f: Fused, cfg: RankingConfig) -> tuple[float, float]:
    """Split a candidate's pre-rerank final score into (rule part, LLM part)."""
    fu, w = cfg.fusion, cfg.fusion.weights
    sig, llm = f.cand.signals, f.llm
    main = llm is not None and llm.main_ok
    clar = llm is not None and llm.clarity_ok and llm.clarity is not None
    rule = llm_part = 0.0
    for dim, wt, rule_v, share, has in (
        ("hook", w.hook, sig.hook, fu.llm_share, main),
        ("cliffhanger", w.cliffhanger, sig.cliffhanger, fu.llm_share, main),
        ("clarity", w.clarity, sig.clarity, fu.clarity_llm_share, clar),
        ("intensity", w.intensity, sig.intensity, fu.llm_share, main),
    ):
        d = f.dims[dim]
        if has:
            r = (1 - share) * rule_v
            rule += wt * r
            llm_part += wt * (d - r)
        else:
            rule += wt * d
    if main and llm and llm.spoiler_risk is not None:
        llm_part -= fu.spoiler_penalty_per_point * llm.spoiler_risk
    return rule, llm_part
