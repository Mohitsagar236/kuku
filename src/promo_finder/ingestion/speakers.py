"""Show-level speaker resolution. Generic: no show-specific alias lists.

Rule: a titled name ``TITLE NAME`` is merged into the bare ``NAME`` only if
  * ``NAME`` itself occurs as a speaker in the show, and
  * exactly ONE titled variant of ``NAME`` occurs (so ``LORD ASHCROFT`` and ``LADY ASHCROFT``
    are never both folded into ``ASHCROFT``, and are never merged with each other).
Anything else is left untouched. Role labels (``THE FORMER GROOM``) are never merged.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from promo_finder.domain.models.timeline import Episode

log = logging.getLogger(__name__)

TITLES = frozenset(
    {
        "LADY", "LORD", "SIR", "DAME", "DUKE", "DUCHESS", "KING", "QUEEN", "PRINCE",
        "PRINCESS", "COUNT", "COUNTESS", "BARON", "BARONESS", "MISS", "MRS", "MR", "MS",
        "DR", "FATHER",
    }
)  # fmt: skip


def _split_title(name: str) -> tuple[str, str] | None:
    words = name.split(" ")
    if len(words) > 1 and words[0] in TITLES:
        return words[0], " ".join(words[1:])
    return None


def build_alias_map(speakers: set[str]) -> dict[str, str]:
    """Return ``{titled_name: bare_name}`` for every safe merge."""
    titled_by_bare: dict[str, set[str]] = defaultdict(set)
    for name in speakers:
        split = _split_title(name)
        if split:
            titled_by_bare[split[1]].add(name)
    aliases: dict[str, str] = {}
    for bare, variants in titled_by_bare.items():
        if bare in speakers and len(variants) == 1:
            aliases[next(iter(variants))] = bare
    return aliases


def resolve_speakers(episodes: list[Episode]) -> dict[str, str]:
    """Apply safe alias merges in place on all beats. Returns the alias map used."""
    speakers = {b.speaker for ep in episodes for sc in ep.scenes for b in sc.beats if b.speaker}
    aliases = build_alias_map(speakers)
    if aliases:
        for ep in episodes:
            for sc in ep.scenes:
                for b in sc.beats:
                    if b.speaker in aliases:
                        b.speaker = aliases[b.speaker]
        log.info("speakers_merged", extra={"count": len(aliases)})
    return aliases
