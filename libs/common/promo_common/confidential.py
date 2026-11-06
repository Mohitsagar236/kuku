"""Guard: the public repo must contain zero script text.

Builds word-shingles (default 8 consecutive words) from the confidential input and scans every
file that WOULD be committed (not git-ignored) for any of them. Findings report file and line
only, never the matching text.
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


def scan_repo(root: Path, shingles: set[int], n: int = 8) -> list[Finding]:
    findings: list[Finding] = []
    for path in tracked_files(root):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = path.read_bytes()
            if b"\x00" in raw[:4096]:
                continue
            text = raw.decode("utf-8", errors="ignore")
        except OSError:
            continue
        by_line: dict[int, list[int]] = {}
        for line, h in scan_text(text, shingles, n):
            by_line.setdefault(line, []).append(h)
        for line, hs in sorted(by_line.items()):
            findings.append(
                Finding(path.relative_to(root).as_posix(), line, f"{hs[0]:016x}", len(hs))
            )
    return findings
