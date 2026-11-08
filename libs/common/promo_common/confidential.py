"""Guard: the public repo must contain zero script text.

Two checks over every file that WOULD be committed (not git-ignored), PDFs included (their text
is extracted and reported by page):

* passage: any run of N consecutive words (default 8) from the confidential input;
* quote: any quoted span ("...", “...”, '...') sharing a run of M words (default 4) with a
  line of DIALOGUE from the input, which catches short quoted lines the 8-word check misses.

Findings report file and line/page only, never the matching text.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pathspec

_WORD = re.compile(r"[a-z0-9']+")
MAX_FILE_BYTES = 2_000_000
_ALWAYS_SKIP_DIRS = {".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    shingle_hash: str  # identifies the matching passage without revealing it
    matches: int = 1  # how many overlapping shingles hit on this line (longer quote = more)
    kind: str = "passage"  # passage | quote
    unit: str = "line"  # line | page (PDFs)

    @property
    def where(self) -> str:
        if self.unit == "line":
            return f"{self.file}:{self.line}"
        return f"{self.file} (page {self.line})"


def _h(words: list[str]) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(words).encode(), digest_size=8).digest(), "big")


def build_shingles(texts: Iterable[str], n: int = 8) -> set[int]:
    out: set[int] = set()
    for t in texts:
        w = _WORD.findall(t.lower())
        for i in range(len(w) - n + 1):
            out.add(_h(w[i : i + n]))
    return out


def tracked_files(root: Path) -> list[Path]:
    """Files git would commit: tracked + untracked-but-not-ignored."""
    if (root / ".git").exists():
        try:
            res = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-co", "--exclude-standard"],
                capture_output=True, text=True, check=True,
            )  # fmt: skip
            return [root / line for line in res.stdout.splitlines() if (root / line).is_file()]
        except (OSError, subprocess.CalledProcessError):
            pass
    ignore = root / ".gitignore"
    lines = ignore.read_text().splitlines() if ignore.is_file() else []
    spec: Any
    try:
        spec = pathspec.PathSpec.from_lines("gitignore", lines)
    except (KeyError, ValueError):  # older pathspec
        spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _ALWAYS_SKIP_DIRS
            and not spec.match_file(f"{rel_dir}/{d}/".lstrip("./") if rel_dir != "." else f"{d}/")
        ]
        for f in filenames:
            rel = f if rel_dir == "." else f"{rel_dir}/{f}"
            if not spec.match_file(rel):
                files.append(Path(dirpath) / f)
    return files


def scan_text(text: str, shingles: set[int], n: int = 8) -> list[tuple[int, int]]:
    """Return (line_no, shingle_hash) for every match in ``text``."""
    hits: list[tuple[int, int]] = []
    words: list[tuple[str, int]] = []
    for ln, line in enumerate(text.splitlines(), start=1):
        words += [(w, ln) for w in _WORD.findall(line.lower())]
    toks = [w for w, _ in words]
    for i in range(len(toks) - n + 1):
        h = _h(toks[i : i + n])
        if h in shingles:
            hits.append((words[i][1], h))
    return hits


_QUOTED = re.compile(
    r'"([^"\n]{3,500}?)"'  # "double"
    r"|\u201c([^\u201c\u201d\n]{3,500}?)\u201d"  # “curly”
    r"|(?<![\w])'([^'\n]{3,500}?)'(?![\w])"  # 'single' (not inside a contraction)
)


def scan_quotes(text: str, quote_shingles: set[int], n: int = 4) -> list[tuple[int, int]]:
    """(line_no, hash) for every quoted span that shares ``n`` consecutive words with dialogue."""
    hits: list[tuple[int, int]] = []
    text = text.replace('"""', "   ").replace("'''", "   ")  # docstring delimiters are not quotes
    for ln, line in enumerate(text.splitlines(), start=1):
        for m in _QUOTED.finditer(line):
            w = _WORD.findall((m.group(1) or m.group(2) or m.group(3)).lower())
            for i in range(len(w) - n + 1):
                h = _h(w[i : i + n])
                if h in quote_shingles:
                    hits.append((ln, h))
                    break
    return hits


def pdf_pages(path: Path) -> list[str]:
    """Text of each PDF page ([] if the file cannot be read as a PDF)."""
    try:
        from pypdf import PdfReader

        return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    except Exception:  # a broken PDF must not crash the guard; it is reported as unreadable
        return []


def _units(path: Path) -> tuple[str, list[str]] | None:
    """('line', [whole text]) for text files, ('page', [page texts]) for PDFs, None to skip."""
    if path.suffix.lower() == ".pdf":
        return "page", pdf_pages(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        return None
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return None
    return "line", [raw.decode("utf-8", errors="ignore")]


def scan_repo(
    root: Path,
    shingles: set[int],
    n: int = 8,
    quote_shingles: set[int] | None = None,
    quote_n: int = 4,
) -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_files(root):
        try:
            got = _units(path)
        except OSError:
            continue
        if got is None:
            continue
        unit, chunks = got
        rel = path.relative_to(root).as_posix()
        for page_no, text in enumerate(chunks, start=1):
            if unit == "page":  # a PDF wraps lines anywhere: scan each page as one line
                text = " ".join(text.split())
            checks = [("passage", scan_text(text, shingles, n))]
            if quote_shingles:
                checks.append(("quote", scan_quotes(text, quote_shingles, quote_n)))
            for kind, hits in checks:
                by_line: dict[int, list[int]] = {}
                for line, h in hits:
                    by_line.setdefault(page_no if unit == "page" else line, []).append(h)
                for line, hs in sorted(by_line.items()):
                    findings.append(Finding(rel, line, f"{hs[0]:016x}", len(hs), kind, unit))
    return findings
