"""In-process pipeline: parse -> candidates -> score (cascade) -> rank -> evaluate.

Same stage functions the services will wrap later. Every stage is deterministic except the LLM
calls, which are cached per candidate.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from candidate_service import GenStats, generate_candidates, load_config, load_lexicon
from evaluation_service import (
    GoldenReport,
    StabilityReport,
    compare_scores,
    evaluate_golden,
    load_golden,
)
from ingestion_service import load_show
from promo_common.config import find_config_dir
from promo_common.llm import (
    BudgetExceeded,
    DailyQuotaExhausted,
    LLMSettings,
    ManagedLLM,
    build_llm,
)
from promo_common.llm.providers.nollm import NoLLM
from promo_common.schemas import Candidate, LLMScores, Show
from ranking_service import RankingConfig, RankResult, load_ranking_config, rank
from ranking_service.fusion import Fused, fuse
from ranking_service.select import select
from scoring_service import Scorer, ScoringConfig, load_scoring_config, rerank

log = logging.getLogger(__name__)
BASELINE_DIR = "rules_baseline"


class RunInterrupted(Exception):
    """Quota/budget stopped the run. Validated results are cached: re-run to resume."""

    def __init__(self, message: str, stats: dict[str, int]) -> None:
        super().__init__(message)
        self.stats = stats


@dataclass
class RunOptions:
    out_root: Path = Path("outputs")
    ablation: bool | None = None
    stability: bool | None = None
    allow_partial: bool = False
    golden_path: Path | None = None
    persist_cache: bool = True
    episodes: set[int] | None = None
    mode: str = "run"  # run | smoke
    write_baseline: bool = True


@dataclass(frozen=True)
class AblationSpec:
    name: str
    label: str
    drop_cues: tuple[str, ...] = ()
    end_weight: float | None = None


@dataclass
class Variant:
    name: str
    cands: list[Candidate]
    gen: GenStats
    scores: dict[str, LLMScores]
    result: RankResult
    scorer: Scorer
    partial: bool = False
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    show: Show
    main: Variant
    baseline: Variant | None
    ablations: list[tuple[AblationSpec, Variant]]
    stability: StabilityReport | None
    golden: GoldenReport | None
    llm: ManagedLLM
    llm_settings: LLMSettings
    configs: dict[str, Any]
    duration_s: float
    out_dir: Path
    partial: bool
    opts: RunOptions


def _eval_cfg() -> dict[str, Any]:
    data = yaml.safe_load((find_config_dir() / "pipeline.yaml").read_text())
    return dict(data.get("evaluation") or {})


def ablation_specs(scfg: ScoringConfig, ecfg: dict[str, Any]) -> list[AblationSpec]:
    known = {
        "literal_cliffhanger": AblationSpec(
            "literal_cliffhanger",
            f"literal script annotation(s) {list(scfg.literal_cues)} removed from rules and prompts",
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
        fused = [fuse(c, interim.get(c.id), rcfg) for c in cs]
        picked, _ = select(fused, show, loose, limit=scfg.cascade_top)
        return [f.cand.id for f in picked]

    partial = False
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
    info = {"terms": terms, "end_weight": ccfg.episode_end_weight, "ranked_candidates": len(ranked)}
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


def run_pipeline(
    show_dir: str | Path,
    opts: RunOptions,
    settings: LLMSettings | None = None,
    progress: Callable[[str], None] = lambda m: None,
    llm: ManagedLLM | None = None,
) -> RunResult:
    t0 = time.perf_counter()
    settings = settings or LLMSettings()
    ecfg = _eval_cfg()
    scfg, rcfg = load_scoring_config(), load_ranking_config()
    show = load_show(show_dir, episodes=opts.episodes)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = opts.out_root / show.slug / (f"smoke-{stamp}" if opts.mode == "smoke" else stamp)
    if llm is None:
        llm = build_llm(settings, raw_dir=out_dir / "llm_raw", persist=opts.persist_cache)
    elif llm.raw_dir is None:
        llm.raw_dir = out_dir / "llm_raw"
    workers = settings.max_concurrency
    progress(f"parsed {len(show.episodes)} episodes; provider={llm.provider} model={llm.model}")

    baseline = run_rules_baseline(show, scfg, rcfg) if opts.write_baseline else None
    if baseline:
        progress(f"rules baseline: {len(baseline.result.promos)} promos (no LLM)")

    llm.phase = "main"
    main = run_variant(
        show, llm, "main", scfg, rcfg, allow_partial=opts.allow_partial, workers=workers
    )
    progress(
        f"main run: {len(main.cands)} candidates first-pass scored, "
        f"{main.info['ranked_candidates']} fully scored, {len(main.result.promos)} promos"
    )

    ablations: list[tuple[AblationSpec, Variant]] = []
    do_abl = ecfg.get("ablation_enabled", True) if opts.ablation is None else opts.ablation
    if do_abl and not main.partial:
        for spec in ablation_specs(scfg, ecfg):
            llm.phase = f"ablation:{spec.name}"
            v = run_variant(
                show, llm, spec.name, scfg, rcfg, allow_partial=opts.allow_partial,
                workers=workers, drop_cues=spec.drop_cues, end_weight=spec.end_weight,
            )  # fmt: skip
            ablations.append((spec, v))
            progress(f"ablation {spec.name}: done")

    stability: StabilityReport | None = None
    do_stab = ecfg.get("stability_enabled", False) if opts.stability is None else opts.stability
    if do_stab and not main.partial:
        llm.phase = "stability"
        stability = run_stability(
            show, llm, main, scfg, rcfg,
            float(ecfg.get("stability_sample_frac", 0.10)),
            int(ecfg.get("stability_seed", 7)),
            workers,
        )  # fmt: skip
        progress("stability re-score done")
    llm.phase = ""

    golden: GoldenReport | None = None
    gpath = opts.golden_path or Path("golden.yaml")
    if gpath.is_file():
        golden = evaluate_golden(
            load_golden(gpath), main.result.promos,
            int(ecfg.get("golden_k", 10)), float(ecfg.get("golden_iou", 0.5)),
        )  # fmt: skip
    configs = {
        "scoring": scfg.model_dump(),
        "ranking": rcfg.model_dump(),
        "candidate": load_config().model_dump(),
        "evaluation": ecfg,
    }
    return RunResult(
        show, main, baseline, ablations, stability, golden, llm, settings, configs,
        time.perf_counter() - t0, out_dir, main.partial, opts,
    )  # fmt: skip
