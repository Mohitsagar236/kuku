"""Package unedited assignment outputs and their provenance for private handoff.

Never includes the screenplay pack, .env, credentials, cache, or editor labels.
Run after `make docs`; artifacts are copied byte-for-byte and checksummed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG = "i-married-a-broken-soldier-he-was-a-duke"


def prepare(run: Path, destination: Path) -> dict:
    metadata = json.loads((run / "run_metadata.json").read_text())
    if metadata["mode"] != "run" or metadata["partial"] or metadata["show"]["episodes"] != 50:
        raise ValueError("Submission requires a complete run over the 50-episode pack")
    files = {}
    for directory, prefix in ((run, "full-run"), (run.parent / "rules_baseline", "rules-baseline")):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in {".json", ".csv", ".md"}:
                files[f"{prefix}/{path.relative_to(directory).as_posix()}"] = path
    for directory in sorted(run.parent.iterdir()):
        if (
            directory.is_dir()
            and not directory.is_symlink()
            and not directory.name.startswith("smoke-")
        ):
            for path in sorted((directory / "llm_raw").glob("*.json")):
                if not path.is_symlink():
                    files[f"provenance/{directory.name}/llm_raw/{path.name}"] = path
    files["solution.pdf"] = ROOT / "docs/SOLUTION_submission.pdf"
    files["completion-checklist.md"] = ROOT / "docs/ASSIGNMENT_COMPLETION.md"
    files["source-validation.json"] = ROOT / "outputs/assignment-audit.json"
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.txt",
            (
                "CONFIDENTIAL - Kuku Problem 1 submission materials.\n"
                "Use only for assignment review; never publish this archive.\n\n"
                f"full-run/: completed 50-episode run {run.name}; copied without edits.\n"
                "rules-baseline/: comparison output.\n"
                "provenance/: unedited full-run model replies, including early failures.\n"
                "The completed run reuses cached judgments, so it has no new raw model responses.\n"
                "solution.pdf: two-page solution with short source excerpts.\n"
                "source-validation.json: deterministic checks against the unchanged source pack.\n"
                "SHA256SUMS.json: checksums of every copied file.\n\n"
                "No human-labelled accuracy claim; context/spoiler flags are model judgments.\n"
            ),
        )
        for name, path in files.items():
            archive.write(path, name)
        archive.writestr("SHA256SUMS.json", json.dumps(manifest, indent=2) + "\n")
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity failed")
        assert all(
            hashlib.sha256(archive.read(name)).hexdigest() == digest
            for name, digest in manifest.items()
        )
    return {
        "archive": str(destination),
        "files": len(files),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=ROOT / "outputs" / SLUG / "20261108-153346")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "outputs/packages/kuku-problem1-submission-20261108.zip"
    )
    args = parser.parse_args()
    print(json.dumps(prepare(args.run, args.out), indent=2))


if __name__ == "__main__":
    main()
