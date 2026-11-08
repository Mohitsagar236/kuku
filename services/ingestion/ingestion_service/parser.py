"""Deterministic screenplay parser (no LLM).

Lenient by design: every problem becomes a ``ParseWarning``; the parser never raises on
content. A beat is only emitted if its start time is trustworthy, so every downstream
timestamp traces back to a real source line.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from promo_common.schemas.timeline import Beat, BeatType, Episode, ParseWarning, Scene
from promo_common.timecode import TimecodeError, parse_timecode

log = logging.getLogger(__name__)

_QUOTES = "\"'“”‘’"

_RE_SCRIPT_START = re.compile(r"^\s*-{2,}\s*script\s+start\s*-{2,}\s*$", re.I)
_RE_SCRIPT_END = re.compile(r"^\s*-{2,}\s*script\s+end\s*-{2,}\s*$", re.I)
_RE_SCENE = re.compile(r"^\s*scene\s+(\d+)\s*$", re.I)
_RE_FIELD = re.compile(r"^\s*(?P<key>[A-Za-z][A-Za-z ]{2,30}?)\s*:\s*(?P<val>.*)$")
_RE_BLOCK = re.compile(r"^\s*\*\*\s*(?P<inner>.+?)\s*\*\*\s*:?\s*$")
_RE_TS = re.compile(
    r"^\s*start\s*:\s*\(?(?P<s>[^|)]*)\)?\s*\|\s*end\s*:\s*\(?(?P<e>[^|)]*)\)?\s*$", re.I
)
_RE_PAREN = re.compile(r"\(([^)]*)\)")
_RE_EP_DIGITS = re.compile(r"(\d+)")
_RE_FILE_NUM = re.compile(r"episode[-_ ]0*(\d+)", re.I)

_TYPE_BY_NAME: dict[str, BeatType] = {
    "CAMERA": BeatType.CAMERA,
    "ACTION": BeatType.ACTION,
    "SOUND FX": BeatType.SOUND_FX,
    "SOUND": BeatType.SOUND_FX,
    "SFX": BeatType.SOUND_FX,
    "SOUND EFFECT": BeatType.SOUND_FX,
    "SOUND EFFECTS": BeatType.SOUND_FX,
    "MUSIC": BeatType.SOUND_FX,
    "SCORE": BeatType.SOUND_FX,
}
_OTHER_BLOCKS = {
    "TRANSITION", "TITLE", "SUPER", "NOTE", "NOTES", "SHOT", "VISUAL", "VFX", "CUT", "FADE",
}  # fmt: skip
_VO_TOKENS = {"V.O.", "V.O", "VO", "VOICE OVER", "VOICEOVER", "VOICE-OVER"}
_OS_TOKENS = {"O.S.", "O.S", "OS", "OFF-SCREEN", "OFFSCREEN", "OFF SCREEN"}
_CONT_TOKENS = {"CONT'D", "CONT.", "CONT", "CONTINUES", "CONTINUED", "CONTINUING"}


@dataclass
class _Block:
    name: str
    line: int
    start: float | None = None
    end: float | None = None
    ts_seen: bool = False
    text_lines: list[str] = field(default_factory=list)


@dataclass
class _SceneBuilder:
    idx: int
    heading: str = ""
    declared_start: float | None = None
    declared_end: float | None = None
    raw: list[tuple[int, Beat]] = field(default_factory=list)  # (source order, beat)


def normalize_text(text: str) -> str:
    """Normalise line endings, BOM, non-breaking spaces and odd whitespace."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return text.replace(" ", " ").replace("​", "")


def normalize_speaker(name: str) -> str:
    """Uppercase and squeeze whitespace. Nothing else.

    Titles (LADY/LORD/...) are NOT stripped here: whether ``LADY JULIANA`` and ``JULIANA``
    are one person can only be decided from the whole show (see ``speakers.resolve_speakers``).
    """
    return " ".join(name.upper().split())


def _split_header(inner: str) -> tuple[str, list[str]]:
    """Split ``JULIANA (V.O.) (Reflective, Vulnerable):`` into name and parentheticals."""
    inner = inner.strip().rstrip(":").strip()
    name = inner.split("(", 1)[0].strip().rstrip(":").strip()
    groups = _RE_PAREN.findall(inner)
    # tolerate an unclosed final parenthesis
    tail = inner.rsplit("(", 1)[-1] if inner.count("(") > inner.count(")") else ""
    if tail:
        groups.append(tail)
    return name, [g for g in groups if g.strip()]


def _classify_parens(groups: list[str]) -> tuple[list[str], bool, bool]:
    tags: list[str] = []
    vo = os_ = False
    for group in groups:
        for tok in re.split(r"[,/;]", group):
            t = tok.strip().rstrip(":").strip()
            if not t:
                continue
            up = t.upper()
            if up in _VO_TOKENS:
                vo = True
            elif up in _OS_TOKENS:
                os_ = True
            elif up in _CONT_TOKENS:
                continue
            else:
                tags.append(t.lower())
    return tags, vo, os_


def _clean_dialogue(text: str) -> str:
    t = " ".join(text.split())
    if len(t) >= 2 and t[0] in _QUOTES and t[-1] in _QUOTES:
        t = t[1:-1].strip()
    elif t and t[0] in _QUOTES and t.count(t[0]) == 1:
        t = t[1:].strip()
    return t


class _EpisodeParser:
    def __init__(self, text: str, source_file: str, fallback_number: int | None) -> None:
        self.lines = normalize_text(text).split("\n")
        self.source_file = source_file
        self.fallback_number = fallback_number
        self.warnings: list[ParseWarning] = []
        self.number: int | None = None
        self.logline = ""
        self.continuity = ""
        self.scenes: list[_SceneBuilder] = []
        self._scene: _SceneBuilder | None = None
        self._block: _Block | None = None
        self._order = 0

    # -- helpers -----------------------------------------------------------------------
    def warn(self, code: str, message: str, line: int | None = None) -> None:
        self.warnings.append(ParseWarning(code=code, message=message, line=line))
        log.warning(
            "parse_warning",
            extra={
                "file": self.source_file,
                "code": code,
                "line_no": line,
            },  # no script text in logs
        )

    def _ts(self, raw: str, what: str, line: int) -> float | None:
        try:
            return parse_timecode(raw)
        except TimecodeError:
            self.warn("malformed_timestamp", f"{what}: {raw.strip()!r}", line)
            return None

    # -- main --------------------------------------------------------------------------
    def parse(self) -> Episode:
        in_script = False
        header_target: str | None = None  # which header field is accepting continuation lines
        has_start_marker = any(_RE_SCRIPT_START.match(ln) for ln in self.lines)
        if not has_start_marker:
            self.warn("missing_script_start", "no '--- SCRIPT START ---' marker found")
        in_script = not has_start_marker
        saw_end = False

        for i, raw_line in enumerate(self.lines, start=1):
            line = raw_line.rstrip()
            if _RE_SCRIPT_END.match(line):
                saw_end = True
                self._flush_block()
                break
            if _RE_SCRIPT_START.match(line):
                in_script = True
                header_target = None
                continue

            if not in_script:
                header_target = self._header_line(line, i, header_target)
                continue
            if self._script_line(line, i):
                continue

        self._flush_block()
        if not saw_end:
            self.warn("missing_script_end", "no '--- SCRIPT END ---' marker found")
        return self._build()

    def _header_line(self, line: str, i: int, target: str | None) -> str | None:
        m = _RE_FIELD.match(line)
        if m:
            key = " ".join(m.group("key").lower().split())
            val = m.group("val").strip()
            if key == "episode number":
                digits = _RE_EP_DIGITS.search(val)
                self.number = int(digits.group(1)) if digits else None
                if self.number is None:
                    self.warn("bad_episode_number", f"no number in {val!r}", i)
                return None
            if key == "episode logline":
                self.logline = val
                return "logline"
            if key == "continuity notes":
                self.continuity = val
                return "continuity"
        if not line.strip():
            return None
        if target == "logline":
            self.logline = f"{self.logline} {line.strip()}".strip()
        elif target == "continuity":
            self.continuity = f"{self.continuity} {line.strip()}".strip()
        return target

    def _script_line(self, line: str, i: int) -> bool:
        m_scene = _RE_SCENE.match(line)
        if m_scene:
            self._flush_block()
            self._scene = _SceneBuilder(idx=int(m_scene.group(1)))
            self.scenes.append(self._scene)
            return True

        m_block = _RE_BLOCK.match(line)
        if m_block:
            self._flush_block()
            self._ensure_scene(i)
            self._block = _Block(name=m_block.group("inner"), line=i)
            return True

        blk = self._block
        if blk is None:
            return self._scene_field(line, i)

        m_ts = _RE_TS.match(line)
        if m_ts and not blk.ts_seen:
            blk.ts_seen = True
            blk.start = self._ts(m_ts.group("s"), "start", i)
            blk.end = self._ts(m_ts.group("e"), "end", i)
            return True
        if line.strip().lower().startswith("start:") and not blk.ts_seen:
            blk.ts_seen = True
            self.warn("malformed_timestamp", f"unparseable time line: {line.strip()[:60]!r}", i)
            return True

        if line.strip():
            blk.text_lines.append(line.strip())
        return True

    def _scene_field(self, line: str, i: int) -> bool:
        m = _RE_FIELD.match(line)
        if m is None:
            if line.strip():
                self.warn("orphan_text", f"text outside any block: {line.strip()[:50]!r}", i)
            return False
        key = " ".join(m.group("key").lower().split())
        val = m.group("val").strip()
        scene = self._ensure_scene(i)
        if key == "scene heading":
            scene.heading = val
        elif key == "scene start":
            scene.declared_start = self._ts(val, "scene start", i)
        elif key == "scene end":
            scene.declared_end = self._ts(val, "scene end", i)
        elif key == "actions and dialogue":
            pass
        else:
            self.warn("orphan_text", f"unknown field {key!r}", i)
        return True

    def _ensure_scene(self, i: int) -> _SceneBuilder:
        if self._scene is None:
            self.warn("implicit_scene", "content before first SCENE marker", i)
            self._scene = _SceneBuilder(idx=1)
            self.scenes.append(self._scene)
        return self._scene

    # -- block -> beat -----------------------------------------------------------------
    def _flush_block(self) -> None:
        blk, self._block = self._block, None
        if blk is None or self._scene is None:
            return
        name, groups = _split_header(blk.name)
        up = name.upper()
        text = " ".join(blk.text_lines).strip()
        if not text:
            self.warn("empty_block", f"block {name!r} has no text", blk.line)
            return
        if blk.start is None:
            self.warn("beat_dropped", f"block {name!r} has no valid start time", blk.line)
            return
        start, end = blk.start, blk.end
        if end is None:
            end = start
            self.warn("end_inferred", f"block {name!r}: end set to start", blk.line)
        elif end < start:
            self.warn("end_before_start", f"block {name!r}: end {end} < start {start}", blk.line)
            end = start

        tags, vo, os_ = _classify_parens(groups)
        speaker = speaker_raw = None
        btype = _TYPE_BY_NAME.get(up)
        if btype is None:
            quoted = text[:1] in _QUOTES
            if up in _OTHER_BLOCKS or (not quoted and not groups):
                btype = BeatType.OTHER
                self.warn("unknown_block_type", f"block type {name!r} kept as 'other'", blk.line)
            else:
                btype = BeatType.DIALOGUE
        if btype is BeatType.DIALOGUE:
            speaker_raw = name
            speaker = normalize_speaker(name)
            text = _clean_dialogue(text)
        else:
            tags, vo, os_ = [], False, False
            if btype is BeatType.OTHER and up not in _OTHER_BLOCKS:
                # unquoted text under a bare label: maybe a speaker. Keep the label so the
                # loader can promote it if that name speaks (quoted) elsewhere in the show.
                speaker_raw = name
        if not text:
            self.warn("empty_block", f"block {name!r} empty after cleanup", blk.line)
            return

        self._order += 1
        # id/seq are assigned in _build() once the final order is known
        self._scene.raw.append(
            (
                self._order,
                Beat(
                    id="tmp",
                    episode_number=0,
                    scene_idx=self._scene.idx,
                    seq=0,
                    type=btype,
                    speaker=speaker,
                    speaker_raw=speaker_raw,
                    emotion_tags=tags,
                    voice_over=vo,
                    off_screen=os_,
                    text=text,
                    start_s=start,
                    end_s=end,
                ),
            )
        )

    # -- assemble ----------------------------------------------------------------------
    def _build(self) -> Episode:
        number = self.number
        file_num = _RE_FILE_NUM.search(self.source_file)
        if number is None:
            number = self.fallback_number or (int(file_num.group(1)) if file_num else None)
            if number is None:
                number = 0
                self.warn("missing_episode_number", "could not determine episode number")
        elif file_num and int(file_num.group(1)) != number:
            self.warn(
                "episode_number_mismatch",
                f"header says {number}, filename says {file_num.group(1)}; using header",
            )

        scenes: list[Scene] = []
        max_end = 0.0
        for sb in self.scenes:
            ordered = sorted(sb.raw, key=lambda t: (t[1].start_s, t[0]))
            beats = [
                b.model_copy(
                    update={
                        "id": f"E{number:03d}-S{sb.idx:02d}-B{seq:03d}",
                        "episode_number": number,
                        "seq": seq,
                    }
                )
                for seq, (_, b) in enumerate(ordered)
            ]
            if not beats:
                self.warn("empty_scene", f"scene {sb.idx} has no beats")
            b_start = min((b.start_s for b in beats), default=0.0)
            b_end = max((b.end_s for b in beats), default=0.0)
            start = sb.declared_start if sb.declared_start is not None else b_start
            end = sb.declared_end if sb.declared_end is not None else b_end
            if end < start:
                self.warn("bad_scene_range", f"scene {sb.idx}: end < start; using beat span")
                start, end = b_start, b_end
            max_end = max(max_end, end, b_end)
            scenes.append(Scene(idx=sb.idx, heading=sb.heading, start_s=start, end_s=end,
                                beats=beats))  # fmt: skip

        if not any(s.beats for s in scenes):
            self.warn("no_beats", "episode produced no usable beats")
        return Episode(
            number=number,
            source_file=self.source_file,
            logline=self.logline,
            continuity_notes=self.continuity,
            duration_s=max_end,
            scenes=scenes,
            warnings=self.warnings,
        )


def parse_episode(text: str, source_file: str = "", fallback_number: int | None = None) -> Episode:
    """Parse one episode file's text into a validated ``Episode``. Never raises on content."""
    return _EpisodeParser(text, source_file, fallback_number).parse()


def promote_unquoted_speakers(episodes: list[Episode]) -> int:
    """Turn unquoted ``**NAME:**`` blocks into dialogue when NAME speaks elsewhere in the show.

    A bare label with unquoted text is ambiguous per file (a ``MONTAGE`` block or a speaker whose
    writer dropped the quote marks and parentheticals). Show-wide evidence settles it: a label
    that is dialogue somewhere else is a speaker; one that never is stays ``other``.
    Returns how many beats were promoted.
    """
    known = {
        normalize_speaker(b.speaker_raw)
        for ep in episodes
        for b in ep.beats
        if b.type is BeatType.DIALOGUE and b.speaker_raw
    }
    n = 0
    for ep in episodes:
        for b in ep.beats:
            if (
                b.type is BeatType.OTHER
                and b.speaker_raw
                and normalize_speaker(b.speaker_raw) in known
            ):
                b.type = BeatType.DIALOGUE
                b.speaker = normalize_speaker(b.speaker_raw)
                b.text = _clean_dialogue(b.text) or b.text
                n += 1
    return n
