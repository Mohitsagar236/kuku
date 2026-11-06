"""Ranking pipeline: fuse -> dedupe/diversity -> cross-show rerank of the shortlist -> finalize."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from promo_common.schemas import Candidate, LLMScores, Promo, Show

from ranking_service.build import FinalizeStats, finalize
from ranking_service.config import RankingConfig
from ranking_service.fusion import Fused, fuse
from ranking_service.select import select

RerankFn = Callable[[list[Fused]], dict[str, float] | None]


@dataclass
class RankResult:
    promos: list[Promo]
    fused: list[Fused]
    selected: list[Fused]
    dropped: Counter[str]
    final_stats: FinalizeStats
    rerank_used: bool
    pre_rerank_top: list[str] = field(default_factory=list)


def apply_rerank(selected: list[Fused], ranks: dict[str, float], cfg: RankingConfig) -> list[Fused]:
    pool = selected[: cfg.rerank.pool]
    n = len(pool)
    for f in pool:
        r = ranks.get(f.cand.id)
        if r is None:
            continue
        f.rerank_rank = r
        rr = 10 - 6 * (r - 1) / max(1, n - 1)
        f.blended = round((1 - cfg.rerank.weight) * f.final + cfg.rerank.weight * rr, 3)
    pool.sort(key=lambda f: (-f.blended, f.cand.id))
    return pool + selected[cfg.rerank.pool :]


def rank(
    show: Show,
    cands: list[Candidate],
    scores: dict[str, LLMScores],
    cfg: RankingConfig,
    rerank_fn: RerankFn | None = None,
) -> RankResult:
    fused = [fuse(c, scores.get(c.id), cfg) for c in cands]
    selected, dropped = select(fused, show, cfg.selection)
    pre = [f.cand.id for f in selected]
    used = False
    if rerank_fn is not None and len(selected) > 1:
        ranks = rerank_fn(selected[: cfg.rerank.pool])
        if ranks:
            selected = apply_rerank(selected, ranks, cfg)
            used = True
    promos, fstats = finalize(show, selected, cfg.trim.apply)
    return RankResult(promos, fused, selected, dropped, fstats, used, pre)
