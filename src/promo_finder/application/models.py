"""Application contracts shared by runners and output adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from promo_finder.candidates import GenStats
from promo_finder.domain.models import Candidate, LLMScores, Show
from promo_finder.evaluation import FeedbackSummary, GoldenReport, StabilityReport
from promo_finder.infrastructure.llm import LLMSettings, ManagedLLM
from promo_finder.ranking import RankResult
from promo_finder.scoring import Scorer

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
    feedback_path: Path | None = None  # default: <out_root>/<show>/editor_feedback.yaml
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
    feedback: FeedbackSummary | None = None
    feedback_error: str = ""
    source_dir: str = ""
