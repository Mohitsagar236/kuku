"""Timestamp parsing and arithmetic. Pure, deterministic, no I/O."""

from __future__ import annotations

import re

_TC_RE = re.compile(r"^\s*(?:(?P<h>\d{1,2}):)?(?P<m>\d{1,3}):(?P<s>\d{1,2}(?:\.\d{1,3})?)\s*$")


class TimecodeError(ValueError):
    """Raised when a timestamp string cannot be parsed."""


def parse_timecode(raw: str) -> float:
    """Parse ``mm:ss.xx`` (or ``h:mm:ss.xx``, ``mm:ss``) into seconds.

    Tolerates surrounding whitespace/parentheses. Raises TimecodeError otherwise.
    """
    if raw is None:
        raise TimecodeError("timestamp is None")
    cleaned = raw.strip().strip("()").strip()
    m = _TC_RE.match(cleaned)
    if not m:
        raise TimecodeError(f"malformed timestamp: {raw!r}")
    hours = int(m.group("h") or 0)
    minutes = int(m.group("m"))
    seconds = float(m.group("s"))
    if seconds >= 60:
        raise TimecodeError(f"seconds out of range in timestamp: {raw!r}")
    return round(hours * 3600 + minutes * 60 + seconds, 3)


def format_timecode(seconds: float) -> str:
    """Format seconds as ``mm:ss.xx`` (centisecond precision)."""
    if seconds < 0:
        raise ValueError("negative time")
    total_cs = round(seconds * 100)
    minutes, cs = divmod(total_cs, 6000)
    return f"{minutes:02d}:{cs // 100:02d}.{cs % 100:02d}"


def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Length in seconds of the intersection of two ranges (0 if disjoint)."""
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    """Intersection-over-union of two time ranges."""
    inter = overlap(a_start, a_end, b_start, b_end)
    union = (a_end - a_start) + (b_end - b_start) - inter
    return inter / union if union > 0 else 0.0
