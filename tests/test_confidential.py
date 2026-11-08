"""The public repo must contain zero script text."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from promo_common.confidential import (
    build_shingles,
    scan_quotes,
    scan_repo,
    scan_text,
    tracked_files,
)

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


def _pdf(text: str) -> bytes:
    """A minimal one-page PDF (Helvetica, uncompressed) whose text pypdf can extract."""
    body = f"BT /F1 12 Tf 40 700 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_pdf_text_is_extracted_and_reported_by_page(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "doc.pdf").write_bytes(_pdf(QUOTE))
    (root / "clean.pdf").write_bytes(_pdf(OTHER))
    findings = scan_repo(root, build_shingles([QUOTE]))
    assert [(f.file, f.line, f.unit) for f in findings] == [("doc.pdf", 1, "page")]
    assert findings[0].where == "doc.pdf (page 1)"


def test_a_broken_pdf_does_not_crash_the_guard(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "bad.pdf").write_bytes(b"%PDF-1.4 not really")
    assert scan_repo(root, build_shingles([QUOTE])) == []


DIALOGUE = "You will never take my brother away from me"


def test_short_quoted_dialogue_is_flagged_but_unquoted_prose_and_docstrings_are_not(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path)
    q = build_shingles([DIALOGUE], 4)
    (root / "quoted.md").write_text('The hook is "never take my brother" and it lands.\n')
    (root / "curly.md").write_text("x\nShe says “you will never take” at 0:03.\n")
    (root / "single.py").write_text("LINE = 'take my brother away'\n")
    (root / "prose.md").write_text("They never take my brother anywhere, unquoted.\n")
    (root / "doc.py").write_text('"""Never take my brother: a docstring, not a quote."""\n')
    (root / "short.md").write_text('He shouts "my brother" twice.\n')  # 2 words: below the limit
    found = scan_repo(root, build_shingles([DIALOGUE], 8), quote_shingles=q, quote_n=4)
    assert sorted((f.file, f.line, f.kind) for f in found) == [
        ("curly.md", 2, "quote"),
        ("quoted.md", 1, "quote"),
        ("single.py", 1, "quote"),
    ]


def test_contractions_do_not_open_a_fake_single_quoted_span() -> None:
    q = build_shingles([DIALOGUE], 4)
    assert scan_quotes("it's what never take my brother isn't", q) == []
