"""Candidate scoring, cascade selection and robustness variants."""

from __future__ import annotations

import logging
import random
from typing import Any

from promo_finder.application.models import AblationSpec, RunInterrupted, Variant
from promo_finder.candidates import generate_candidates, load_config, load_lexicon
from promo_finder.domain.models import Candidate, LLMScores, Show
from promo_finder.evaluation import StabilityReport, compare_scores
from promo_finder.infrastructure.llm import BudgetExceeded, DailyQuotaExhausted, ManagedLLM
from promo_finder.infrastructure.llm.providers.nollm import NoLLM
from promo_finder.ranking import RankingConfig, rank
from promo_finder.ranking.fusion import Fused, fuse
from promo_finder.ranking.select import select
from promo_finder.scoring import Scorer, ScoringConfig, rerank

log = logging.getLogger(__name__)


def ablation_specs(scfg: ScoringConfig, ecfg: dict[str, Any]) -> list[AblationSpec]:
    known = {
        "literal_cliffhanger": AblationSpec(
            "literal_cliffhanger",
            f"literal annotations {list(scfg.literal_cues)} removed from rules and prompts",
            drop_cues=tuple(scfg.literal_cues),
        ),
        "episode_end_prior": AblationSpec(
            "episode_end_prior", "episode-end prior switched off (weight 0)", end_weight=0.0
        ),
    }
    names = ecfg.get("ablations", list(known))
    return [known[n] for n in names if n in known]


def run_variant(
    show: Show,
    llm: ManagedLLM,
    name: str,
    scfg: ScoringConfig,
    rcfg: RankingConfig,
    *,
    allow_partial: bool,
    workers: int,
    drop_cues: tuple[str, ...] = (),
    end_weight: float | None = None,
    cascade: bool = True,
) -> Variant:
    ccfg = load_config()
    if end_weight is not None:
        ccfg = ccfg.model_copy(update={"episode_end_weight": end_weight})
    terms = list(drop_cues)
    cands, gen = generate_candidates(show, ccfg, load_lexicon(drop_cues=terms))
    scorer = Scorer(llm, show, scfg, redact_terms=terms, workers=workers)

    use_cascade = cascade and scfg.cascade_top > 0
    cap = rcfg.selection

    def shortlist(cs: list[Candidate], interim: dict[str, LLMScores]) -> list[str]:
        # same dedupe/diversity logic as the final ranking, with looser caps for headroom
        loose = cap.model_copy(
            update={
                "max_per_episode": cap.max_per_episode * 2,
                "max_per_scene": cap.max_per_scene * 2,
                "max_per_arc": cap.max_per_arc * 2,
                "output_count": scfg.cascade_top,
            }
        )
        floor = rcfg.gates.min_cliffhanger
        considered: list[Fused] = []
        for c in cs:
            s = interim.get(c.id)
            if s is not None and s.main_ok and (s.hook or 0) < rcfg.gates.min_hook:
                gate_hook_first_pass.add(c.id)  # hook does not depend on the end: gate it now
                continue
            if s is not None and s.main_ok and c.kind == "single" and scfg.refine_ends:
                # Refinement can fix a weak ending: keep it eligible for the shortlist.
                s = s.model_copy(update={"cliffhanger": max(s.cliffhanger or 0.0, floor)})
            considered.append(fuse(c, s, rcfg))
        picked, _ = select(considered, show, loose, limit=scfg.cascade_top)
        return [f.cand.id for f in picked]

    partial = False
    gate_hook_first_pass: set[str] = set()
    try:
        scores = scorer.score(cands, shortlist=shortlist if use_cascade else None)
    except (DailyQuotaExhausted, BudgetExceeded) as e:
        msg = f"{type(e).__name__}: {e}"
        if not allow_partial:
            raise RunInterrupted(msg, dict(scorer.stats)) from e
        log.warning("partial_run", extra={"reason": msg})
        scorer.offline, partial = True, True
        scores = scorer.score(cands, shortlist=shortlist if use_cascade else None)

    ranked = [c for c in cands if scores[c.id].stage2] if use_cascade else cands

    def rerank_fn(pool: list[Fused]) -> dict[str, float] | None:
        nonlocal partial
        if partial:
            return None
        digests = {f.cand.id: scorer.digest(f.cand, f.llm) for f in pool}
        try:
            return rerank(llm, scfg, digests, {f.cand.id: f.final for f in pool})
        except (DailyQuotaExhausted, BudgetExceeded) as e:
            if not allow_partial:
                raise RunInterrupted(f"{type(e).__name__}: {e}", dict(scorer.stats)) from e
            partial = True
            return None

    result = rank(show, ranked, scores, rcfg, rerank_fn)
    info = {
        "terms": terms,
        "end_weight": ccfg.episode_end_weight,
        "ranked_candidates": len(ranked),
        "gate_hook_first_pass": len(gate_hook_first_pass),
    }
    return Variant(name, cands, gen, scores, result, scorer, partial, info)


def run_rules_baseline(show: Show, scfg: ScoringConfig, rcfg: RankingConfig) -> Variant:
    """Rules only: no LLM, no cascade. The yardstick for 'does the LLM add anything?'."""
    return run_variant(
        show, ManagedLLM(NoLLM()), "rules_baseline", scfg, rcfg,
        allow_partial=True, workers=1, cascade=False,
    )  # fmt: skip


def run_stability(
    show: Show,
    llm: ManagedLLM,
    main: Variant,
    scfg: ScoringConfig,
    rcfg: RankingConfig,
    frac: float,
    seed: int,
    workers: int,
) -> StabilityReport:
    rng = random.Random(seed)
    ids = sorted(c.id for c in main.cands if main.scores[c.id].stage2)
    sample = set(rng.sample(ids, max(5, round(len(ids) * frac)) if len(ids) >= 5 else len(ids)))
    top10 = set(main.result.pre_rerank_top[:10])
    by_id = {c.id: c for c in main.cands}
    subset = [by_id[i] for i in sorted(sample | top10)]
    scorer = Scorer(llm, show, scfg, namespace="rescore", workers=workers)
    again = scorer.score(subset, bypass_cache=True)
    rep = StabilityReport(n_sampled=len(subset), n_compared=0, seed=seed, top10_rescored=len(top10))
    rep.per_dim = compare_scores({i: main.scores[i] for i in sample}, again)
    rep.n_compared = max((d.n for d in rep.per_dim.values()), default=0)
    merged = {**main.scores, **{k: v for k, v in again.items() if v.main_ok}}
    ranked = [c for c in main.cands if main.scores[c.id].stage2]
    fused = [fuse(c, merged.get(c.id), rcfg) for c in ranked]
    new_top, _ = select(fused, show, rcfg.selection)
    rep.top10_changed = len(top10 - {f.cand.id for f in new_top[:10]})
    rep.note = "Top-10 change is measured on the pre-rerank ranking; rerank adds its own variation."
    return rep
