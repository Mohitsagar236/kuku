"""File repository for immutable pipeline artifacts. No HTTP dependencies."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from promo_finder.domain.models import Promo

log = logging.getLogger(__name__)
DOWNLOADS = {"promos.csv": "text/csv", "ranking.md": "text/markdown"}
PRIVATE_PREFIX = (
    "upload-"  # runs from the in-app upload: never listed, reachable only by their link
)


def _kind(run_dir: Path) -> str:
    if run_dir.name == "rules_baseline":
        return "baseline"
    return "smoke" if run_dir.name.startswith("smoke-") else "full"


class RunArtifact(BaseModel):
    """Validate stored JSON before any of it reaches the viewer."""

    model_config = ConfigDict(strict=True, extra="allow")

    show: str | None = None
    generated_at: str | None = None
    model: str | None = None
    partial: bool = False
    promos: list[Promo]


def _load_artifact(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    artifact = RunArtifact.model_validate(payload)
    if any(not promo.segments for promo in artifact.promos):
        raise ValueError("promo has no source segments")
    return artifact.model_dump(mode="json")


class RunRepository:
    def __init__(self, outputs: Path) -> None:
        self.outputs = outputs.resolve()

    def _describe(self, f: Path, outputs: Path) -> dict[str, Any] | None:
        d = f.parent
        if f.is_symlink() or outputs.resolve() not in f.resolve().parents:
            return None
        try:
            meta = _load_artifact(f)
            promos = meta["promos"]
        except (OSError, ValueError, AttributeError):
            log.warning("skipping unreadable run %s", d.relative_to(outputs).as_posix())
            return None
        return {
            "id": d.relative_to(outputs).as_posix(),
            "show": meta.get("show") or d.parent.name,
            "kind": _kind(d),
            "generated_at": meta.get("generated_at"),
            "model": meta.get("model"),
            "partial": bool(meta.get("partial")),
            "count": len(promos),
        }

    def describe(self, run_id: str) -> dict[str, Any] | None:
        """The listing entry for one run id (used for unlisted upload runs)."""
        d = self.run_dir(run_id)
        return self._describe(d / "promos.json", self.outputs) if d else None

    def list_runs(self, outputs: Path | None = None, extra: str = "") -> list[dict[str, Any]]:
        """Listed runs; upload runs stay unlisted except the caller's own ``extra`` link."""
        outputs = outputs or self.outputs
        runs = []
        for f in outputs.glob("*/*/promos.json"):
            if f.parent.parent.name.startswith(PRIVATE_PREFIX):
                continue
            if (entry := self._describe(f, outputs)) is not None:
                runs.append(entry)
        if extra.startswith(PRIVATE_PREFIX) and (entry := self.describe(extra)) is not None:
            runs.append(entry)
        runs.sort(key=lambda r: r["generated_at"] or "", reverse=True)
        return runs

    def run_dir(self, run_id: str, outputs: Path | None = None) -> Path | None:
        """Resolve a run id to its folder, or None if it is not a run inside outputs/."""
        outputs = outputs or self.outputs
        if (
            not run_id
            or "\x00" in run_id
            or "\\" in run_id
            or len(Path(run_id).parts) != 2
            or any(p in (".", "..") for p in run_id.split("/"))
        ):
            return None
        d = (outputs / run_id).resolve()
        if (
            outputs not in d.parents
            or not (d / "promos.json").is_file()
            or (d / "promos.json").is_symlink()
        ):
            return None
        return d

    def load_run(self, run_id: str, outputs: Path | None = None) -> dict[str, Any] | None:
        d = self.run_dir(run_id, outputs)
        if d is None:
            return None
        variants = {}
        for f in sorted(d.glob("promos*.json")):
            name = "main" if f.name == "promos.json" else f.stem.removeprefix("promos_")
            if f.is_symlink():
                continue
            variants[name] = _load_artifact(f)
        report = d / "quality_report.md"
        return {
            "id": run_id,
            "variants": variants,
            "report": report.read_text() if report.is_file() and not report.is_symlink() else None,
            "downloads": sorted(
                n for n in DOWNLOADS if (d / n).is_file() and not (d / n).is_symlink()
            ),
        }
