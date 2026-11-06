"""The public repo must contain zero script text."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from promo_common.confidential import build_shingles, scan_repo, scan_text, tracked_files

QUOTE = "the lighthouse keeper whispered a secret that nobody on the harbor ever dared repeat"
OTHER = "short phrase only"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".gitignore").write_text("outputs/\n*.secret\n.venv/\n")
    return tmp_path


def test_shingle_scan_finds_a_long_quote_and_reports_location_not_text(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text(
        f'intro line\nas the script says, "{QUOTE.title()}!" end\n'
    )
    findings = scan_repo(root, build_shingles([QUOTE]))
    assert [(f.file, f.line) for f in findings] == [("docs/notes.md", 2)]
    assert QUOTE.split()[0] not in repr(findings[0].shingle_hash)  # hash only


def test_quote_split_across_lines_is_still_found(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "a.py").write_text(
        "# the lighthouse keeper whispered\n# a secret that nobody on\n# the harbor ever dared repeat\n"
    )
    assert scan_repo(root, build_shingles([QUOTE]))


def test_ignored_and_short_text_do_not_trip_it(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "outputs").mkdir()
    (root / "outputs" / "promos.json").write_text(QUOTE)  # gitignored: allowed to hold script text
    (root / "x.secret").write_text(QUOTE)
    (root / "ok.md").write_text(f"{OTHER}. The lighthouse keeper whispered.")  # only 4 shared words
    assert scan_repo(root, build_shingles([QUOTE])) == []
    assert {p.name for p in tracked_files(root)} == {".gitignore", "ok.md"}


def test_scan_text_ignores_case_and_punctuation() -> None:
    sh = build_shingles([QUOTE])
    assert scan_text(
        "The LIGHTHOUSE keeper -- whispered, a secret: that nobody on the harbor ever dared", sh
    )


def test_repo_gitignore_protects_outputs_and_caches() -> None:
    ignore = (Path(__file__).parent.parent / ".gitignore").read_text()
    for entry in ("outputs/", ".cache/", "llm_raw/", ".env", "golden.yaml"):
        assert entry in ignore, entry


def test_real_repo_contains_no_script_text() -> None:
    """Runs only where the confidential input exists (the author's machine)."""
    candidates = [
        os.environ.get("PF_REAL_SHOW_DIR", ""),
        str(Path.home() / "Desktop/code/kuku/I Married a Broken Soldier. He was a Duke"),
    ]
    src = next((c for c in candidates if c and Path(c).is_dir()), None)
    if src is None:
        pytest.skip("confidential input not present on this machine")
    from ingestion_service import load_show

    show = load_show(src)
    texts = [b.text for e in show.episodes for b in e.beats]
    texts += [e.logline for e in show.episodes] + [e.continuity_notes for e in show.episodes]
    root = Path(__file__).parent.parent
    findings = scan_repo(root, build_shingles(texts))
    assert findings == [], [f"{f.file}:{f.line}" for f in findings]
