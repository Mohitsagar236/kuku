"""Load a folder of episode .txt files into a ``Show``."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from promo_common.config import get_settings
from promo_common.errors import IngestionError
from promo_common.schemas.timeline import Episode, ParseWarning, Show

from ingestion_service.parser import parse_episode
from ingestion_service.speakers import resolve_speakers

log = logging.getLogger(__name__)

_FILE_NUM = re.compile(r"episode[-_ ]0*(\d+)", re.I)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "show"


def parse_episode_spec(spec: str | None) -> set[int] | None:
    """'1-3' -> {1,2,3}; '1,4,7-9' -> {1,4,7,8,9}; ''/None/'all' -> None (every episode)."""
    if spec is None or spec.strip().lower() in ("", "all"):
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        try:
            a, b = int(lo), int(hi or lo)
        except ValueError as e:
            raise IngestionError(f"bad episode spec {part!r} (use e.g. 1-3 or 1,4,7-9)") from e
        if a < 1 or b < a:
            raise IngestionError(f"bad episode range {part!r}")
        out.update(range(a, b + 1))
    return out


def load_show(
    folder: str | Path,
    max_file_bytes: int | None = None,
    episodes: set[int] | None = None,
) -> Show:
    root = Path(folder)
    if not root.is_dir():
        raise IngestionError(f"not a directory: {root}")
    limit = max_file_bytes or get_settings().max_episode_file_bytes

    files = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
    if not files:
        raise IngestionError(f"no .txt episode files in {root}")

    digest = hashlib.sha256()
    warnings: list[ParseWarning] = []
    by_number: dict[int, Episode] = {}

    for path in files:
        size = path.stat().st_size
        if size > limit:
            warnings.append(
                ParseWarning(code="file_too_large", message=f"{path.name}: {size} bytes, skipped")
            )
            continue
        raw = path.read_bytes()
        m = _FILE_NUM.search(path.name)
        ep = parse_episode(
            raw.decode("utf-8", errors="replace"),
            source_file=path.name,
            fallback_number=int(m.group(1)) if m else None,
        )
        if episodes is not None and ep.number not in episodes:
            continue
        digest.update(path.name.encode())
        digest.update(raw)
        if ep.number in by_number:
            warnings.append(
                ParseWarning(
                    code="duplicate_episode",
                    message=f"{path.name}: episode {ep.number} already loaded, skipped",
                )
            )
            continue
        by_number[ep.number] = ep

    if not by_number:
        raise IngestionError(
            f"no matching episodes in {root}" if episodes else f"no parseable episodes in {root}"
        )

    episodes = [by_number[n] for n in sorted(by_number)]
    aliases = resolve_speakers(episodes)
    all_beats = [b for e in episodes for b in e.beats]
    dialogue = sum(1 for b in all_beats if b.type.value == "dialogue")
    if all_beats and dialogue / len(all_beats) < 0.05:
        warnings.append(
            ParseWarning(
                code="low_dialogue_ratio",
                message=f"only {dialogue}/{len(all_beats)} beats are dialogue: format drift?",
            )
        )
    log.info("show_loaded", extra={"episodes": len(episodes), "folder": root.name})
    return Show(
        slug=slugify(root.name),
        title=root.name,
        source_hash=digest.hexdigest(),
        episodes=episodes,
        speaker_aliases=aliases,
        warnings=warnings,
    )
