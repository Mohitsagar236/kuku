"""`make package-outputs`: zip the latest run (+ rules baseline) for PRIVATE upload."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

from promo_finder_cli.pipeline import BASELINE_DIR

NOTE = (
    "CONFIDENTIAL: contains quotes from Kuku's screenplay.\n"
    "Upload only through the private submission link. Never commit or publish this archive.\n"
)


def _latest_run(show_dir: Path, include_smoke: bool) -> Path | None:
    runs = [
        d for d in show_dir.iterdir()
        if d.is_dir() and d.name != BASELINE_DIR and (include_smoke or not d.name.startswith("smoke-"))
    ]  # fmt: skip
    return max(runs, key=lambda d: d.name.removeprefix("smoke-")) if runs else None


def package_outputs(out_root: Path, show: str | None = None, include_smoke: bool = False) -> Path:
    shows = (
        [d for d in out_root.iterdir() if d.is_dir() and d.name != "packages"]
        if out_root.is_dir()
        else []
    )
    if show:
        shows = [d for d in shows if d.name == show]
    if not shows:
        raise FileNotFoundError(f"no outputs found under {out_root} (run `make run` first)")
    if len(shows) > 1:
        raise ValueError(
            f"several shows in {out_root}: pass --show <slug> ({', '.join(d.name for d in shows)})"
        )
    show_dir = shows[0]
    run = _latest_run(show_dir, include_smoke)
    if run is None:
        raise FileNotFoundError(f"no completed runs in {show_dir}")
    pkg_dir = out_root / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    dest = pkg_dir / f"{show_dir.name}-{run.name}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("CONFIDENTIAL.txt", NOTE)
        for f in sorted(run.rglob("*")):
            if f.is_file():
                z.write(f, f"{run.name}/{f.relative_to(run).as_posix()}")
        base = show_dir / BASELINE_DIR
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    z.write(f, f"{BASELINE_DIR}/{f.relative_to(base).as_posix()}")
    return dest


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
