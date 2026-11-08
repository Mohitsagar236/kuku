"""Application orchestration independent of CLI and HTTP transports."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from promo_finder.application.models import AblationSpec, RunOptions, RunResult, Variant
from promo_finder.application.variants import (
    ablation_specs,
    run_rules_baseline,
    run_stability,
    run_variant,
)
from promo_finder.candidates import load_config
from promo_finder.domain.errors import IngestionError
from promo_finder.evaluation import (
    FeedbackError,
    FeedbackSummary,
    GoldenReport,
    StabilityReport,
    evaluate_feedback,
    evaluate_golden,
    load_feedback,
    load_golden,
)
from promo_finder.infrastructure.config import find_config_dir
from promo_finder.infrastructure.llm import LLMSettings, ManagedLLM, build_llm
from promo_finder.ingestion import load_show
from promo_finder.ranking import load_ranking_config
from promo_finder.scoring import load_scoring_config


def _eval_cfg() -> dict[str, Any]:
    data = yaml.safe_load((find_config_dir() / "pipeline.yaml").read_text())
    return dict(data.get("evaluation") or {})


def _reserve_dir(parent: Path, name: str) -> Path:
    """Create a fresh run folder; two runs in the same second get name, name-2, name-3...

    mkdir(exist_ok=False) makes the claim atomic, so a second run can never write
    into (and silently overwrite) an earlier run's outputs.
    """
    parent.mkdir(parents=True, exist_ok=True)
    for i in range(1, 1000):
        d = parent / (name if i == 1 else f"{name}-{i}")
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise RuntimeError(f"could not reserve an output folder for {name} under {parent}")


def _run_pipeline(
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
    out_dir = _reserve_dir(
        opts.out_root / show.slug, f"smoke-{stamp}" if opts.mode == "smoke" else stamp
    )
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
    feedback: FeedbackSummary | None = None
    feedback_error = ""
    fpath = opts.feedback_path or (opts.out_root / show.slug / "editor_feedback.yaml")
    try:
        entries = load_feedback(fpath)
        if entries:
            feedback = evaluate_feedback(entries, main.result.promos, int(ecfg.get("golden_k", 10)))
    except FeedbackError as e:  # a broken editor file must never abort a run
        feedback_error = str(e)
    configs = {
        "scoring": scfg.model_dump(),
        "ranking": rcfg.model_dump(),
        "candidate": load_config().model_dump(),
        "evaluation": ecfg,
    }
    return RunResult(
        show, main, baseline, ablations, stability, golden, llm, settings, configs,
        time.perf_counter() - t0, out_dir, main.partial, opts,
        feedback, feedback_error, str(Path(show_dir).resolve()),
    )  # fmt: skip


def run_pipeline(
    show_dir: str | Path,
    opts: RunOptions,
    settings: LLMSettings | None = None,
    progress: Callable[[str], None] = lambda message: None,
    llm: ManagedLLM | None = None,
) -> RunResult:
    """Run analysis; callers own successful result clients and injected clients."""
    # Reject unusable labels before allocating output folders or making billable calls.
    golden = opts.golden_path or Path("golden.yaml")
    if opts.golden_path is not None and not golden.is_file():
        raise IngestionError(f"golden file not found: {golden}")
    if golden.is_file():
        load_golden(golden)
    owned = llm is None
    settings = settings or LLMSettings()
    if llm is None:
        llm = build_llm(settings, persist=opts.persist_cache)
    try:
        return _run_pipeline(show_dir, opts, settings, progress, llm)
    except BaseException:
        if owned:
            llm.client.close()
        raise
