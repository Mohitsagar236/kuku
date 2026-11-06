"""Render beats for prompts. Optional redaction supports the 'literal annotation' ablation."""

from __future__ import annotations

import re
from collections.abc import Callable

from promo_common.schemas import Beat, BeatType

from scoring_service.ids import CandView, IdMap

Redactor = Callable[[str], str]


def make_redactor(terms: list[str]) -> Redactor:
    if not terms:
        return lambda s: s
    alts = "|".join(re.escape(t) for t in terms)
    rx = re.compile(rf"(?<!\w)(?:{alts})(?!\w)", re.I)

    def redact(s: str) -> str:
        new, n = rx.subn("", s)
        # byte-identical when nothing matched, so unaffected candidates keep their cache key
        return " ".join(new.split()) if n else s

    return redact


def fmt_t(t: float) -> str:
    return f"{int(t // 60):02d}:{t % 60:04.1f}"


def _who(b: Beat) -> str:
    if b.type is BeatType.DIALOGUE:
        tags = f" ({', '.join(b.emotion_tags)})" if b.emotion_tags else ""
        return f"{b.speaker}{tags}: "
    return "[action] " if b.type is BeatType.ACTION else "[sound] "


def beat_line(b: Beat, ids: IdMap, red: Redactor) -> str:
    return f"[{ids.to_short[b.id]} {fmt_t(b.start_s)}] {_who(b)}{red(b.text)}"


def blind_text(v: CandView, red: Redactor) -> str:
    """What a stranger sees: speaker names, tone tags, actions. No ids, times or context."""
    parts = []
    for sv in v.segs:
        parts.append("\n".join(f"{_who(b)}{red(b.text)}" for b in sv.beats))
    return "\n--- CUT ---\n".join(parts)


def candidate_header(label: str, v: CandView, ids: IdMap) -> str:
    segs = " + ".join(f"ep{sv.episode} {fmt_t(sv.start)}-{fmt_t(sv.end)}" for sv in v.segs)
    first = [ids.to_short[sv.beats[0].id] for sv in v.segs if sv.beats]
    last = [ids.to_short[sv.beats[-1].id] for sv in v.segs if sv.beats]
    ranges = "; ".join(f"{a}..{b}" for a, b in zip(first, last, strict=False))
    return (
        f"{label.upper()} ({v.cand.kind}, {v.cand.duration_s:.0f}s, {segs})\n"
        f"  beats: {ranges}\n"
        f"  hook-eligible (first 3s): {', '.join(v.hook_ok)}\n"
        f"  cliff-eligible (final seconds): {', '.join(v.cliff_ok)}"
    )
