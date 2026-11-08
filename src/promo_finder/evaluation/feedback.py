"""Editor feedback: Approve / Would post / Reject verdicts, cut nudges, and precision against them.

Stored per show in ``outputs/<show>/editor_feedback.yaml`` (git-ignored with outputs/).

The file is golden-set compatible: ``promos:`` lists the editor's positives (approve and
would-post) with their FINAL cut in the same shape ``golden.yaml`` uses, so ``load_golden`` reads
it directly. ``rejected:`` and ``pending:`` (a nudged or annotated promo with no verdict yet) are
extra keys the golden loader ignores. Nothing here labels anything: every verdict and note comes
from the editor.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError

from promo_finder.domain.models import Segment, Show
from promo_finder.domain.timecode import TimecodeError, format_timecode, parse_timecode
from promo_finder.domain.validation import validate_segments
from promo_finder.evaluation.matching import promo_iou

Verdict = Literal["approve", "would_post", "reject"]
VERDICTS: tuple[str, ...] = ("approve", "would_post", "reject")
POSITIVE = ("approve", "would_post")
NUDGE_STEP_S = 0.5
MATCH_IOU = 0.5
MAX_NOTE_CHARS = 1000
LOCK = threading.Lock()  # one writer at a time (the viewer server is threaded)


class FSeg(BaseModel):
    episode: int
    start_s: float
    end_s: float


class Entry(BaseModel):
    promo_id: str
    run: str = ""
    verdict: Verdict | None = None
    note: str = ""
    original: list[FSeg]  # the tool's proposal
    segments: list[FSeg]  # the editor's final cut (== original until nudged)
    updated_at: str = ""

    @property
    def nudged(self) -> bool:
        return [(s.episode, s.start_s, s.end_s) for s in self.original] != [
            (s.episode, s.start_s, s.end_s) for s in self.segments
        ]


class FeedbackError(ValueError):
    """The feedback file or a request is malformed. The file is never overwritten in that case."""


# ---------------------------------------------------------------------------- file format


def _t(v: Any) -> float:
    return float(v) if isinstance(v, int | float) else parse_timecode(str(v))


def _segs_out(segs: Sequence[FSeg]) -> list[dict[str, Any]]:
    return [
        {"episode": s.episode, "start": format_timecode(s.start_s), "end": format_timecode(s.end_s)}
        for s in segs
    ]


def _segs_in(raw: Any) -> list[FSeg]:
    return [
        FSeg(episode=int(s["episode"]), start_s=_t(s["start"]), end_s=_t(s["end"])) for s in raw
    ]


def load_feedback(path: Path) -> list[Entry]:
    """All entries in the file ([] if it does not exist). Raises FeedbackError if malformed."""
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text()) or {}
        rows = [r for key in ("promos", "rejected", "pending") for r in (data.get(key) or [])]
        out = []
        for r in rows:
            segs = _segs_in(r["segments"])
            out.append(
                Entry(
                    promo_id=str(r.get("promo_id", "")),
                    run=str(r.get("run", "")),
                    verdict=r.get("verdict"),
                    note=str(r.get("note", "")),
                    original=(
                        _segs_in(r["original_segments"]) if r.get("original_segments") else segs
                    ),
                    segments=segs,
                    updated_at=str(r.get("updated_at", "")),
                )
            )
        return out
    except (yaml.YAMLError, AttributeError, KeyError, TypeError, ValueError, TimecodeError,
            ValidationError) as e:  # fmt: skip
        raise FeedbackError(f"{path.name} is malformed ({type(e).__name__}: {str(e)[:120]})") from e


def _row(e: Entry) -> dict[str, Any]:
    row: dict[str, Any] = {
        "promo_id": e.promo_id,
        "verdict": e.verdict,
        "segments": _segs_out(e.segments),
        "note": e.note,
        "run": e.run,
        "updated_at": e.updated_at,
    }
    if e.nudged:
        row["original_segments"] = _segs_out(e.original)
    return row


def save_feedback(path: Path, show: str, entries: Sequence[Entry]) -> None:
    doc = {
        "version": 1,
        "show": show,
        "purpose": "Editor verdicts from the viewer. `promos` is golden-set compatible.",
        "promos": [_row(e) for e in entries if e.verdict in POSITIVE],
        "rejected": [_row(e) for e in entries if e.verdict == "reject"],
        "pending": [_row(e) for e in entries if e.verdict is None],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    tmp.replace(path)  # atomic: a crash never leaves a half-written file


# ---------------------------------------------------------------------------- promo views


@dataclass(frozen=True)
class PromoView:
    rank: int
    id: str
    segs: tuple[FSeg, ...]
    score: float | None = None


def promo_views(promos: Sequence[Any]) -> list[PromoView]:
    """Accept ranked ``Promo`` models or the dicts in promos.json."""
    out = []
    for p in promos:

        def g(k: str, item: Any = p) -> Any:
            return item[k] if isinstance(item, dict) else getattr(item, k)

        segs = tuple(
            FSeg(
                episode=(s["episode"] if isinstance(s, dict) else s.episode),
                start_s=(s["start_s"] if isinstance(s, dict) else s.start_s),
                end_s=(s["end_s"] if isinstance(s, dict) else s.end_s),
            )
            for s in g("segments")
        )
        out.append(PromoView(int(g("rank")), str(g("id")), segs, g("final_score")))
    return out


def find_entry(entries: Sequence[Entry], promo: PromoView, thr: float = MATCH_IOU) -> Entry | None:
    """The entry for this promo: same id, else the best cut overlap (survives a re-run)."""
    for e in entries:
        if e.promo_id == promo.id:
            return e
    best, hit = thr, None
    for e in entries:
        v = max(promo_iou(promo.segs, e.original), promo_iou(promo.segs, e.segments))
        if v >= best:
            best, hit = v, e
    return hit


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def set_review(
    entries: list[Entry],
    promo: PromoView,
    run: str,
    verdict: str | None,
    note: str | None,
) -> list[Entry]:
    """Set or clear a verdict and/or note. ``verdict=None`` keeps the current one,
    ``"clear"`` removes it. An entry with nothing left (no verdict, note or nudge) is dropped."""
    if verdict not in (None, "clear", *VERDICTS):
        raise FeedbackError(f"verdict must be one of {', '.join(VERDICTS)} or clear")
    if note is not None and len(note) > MAX_NOTE_CHARS:
        raise FeedbackError(f"note is longer than {MAX_NOTE_CHARS} characters")
    e = find_entry(entries, promo)
    if e is None:
        e = Entry(promo_id=promo.id, run=run, original=list(promo.segs), segments=list(promo.segs))
        entries = [*entries, e]
    if verdict == "clear":
        e.verdict = None
    elif verdict is not None:
        e.verdict = verdict  # type: ignore[assignment]
    if note is not None:
        e.note = note.strip()
    e.run, e.updated_at = run, _now()
    return [x for x in entries if x.verdict or x.note or x.nudged]


# ---------------------------------------------------------------------------- nudges

_WHY = {
    "start_not_on_beat": "does not fall on the start of a line",
    "end_not_on_beat": "does not fall on the end of a line",
    "cut_inside_beat": "would cut inside a line of dialogue or action",
    "duration_out_of_range": "would make the promo shorter than 20 s or longer than 60 s",
    "no_hook_in_first_window": "leaves nothing starting in the first 3 s to hook the viewer",
    "segment_too_short": "would make a segment shorter than 3 s",
    "out_of_order": "would put the segments out of order",
}


def _plain(codes: list[str]) -> list[str]:
    out = []
    for c in codes:
        out.append(_WHY.get(c.split(":", 1)[-1], c))
    return list(dict.fromkeys(out))


@dataclass
class NudgeResult:
    ok: bool
    segments: list[FSeg]
    reasons: list[str] = field(default_factory=list)
    suggestions: list[float] = field(default_factory=list)


def _violations(show: Show, segs: Sequence[FSeg]) -> list[str]:
    try:
        cuts = [
            Segment(episode_number=s.episode, start_s=s.start_s, end_s=s.end_s,
                    start_beat_id="", end_beat_id="")
            for s in segs
        ]  # fmt: skip
    except ValueError:
        return ["start would pass the end"]
    return validate_segments(show, cuts)


def nudge(
    show: Show,
    segs: Sequence[FSeg],
    index: int,
    edge: str,
    delta: float | None = None,
    to: float | None = None,
) -> NudgeResult:
    """Move one cut point by ``delta`` seconds (or to ``to``) and re-validate with the SAME
    rules the tool delivers under (beat boundaries, not inside a line, 20-60 s, hook in the
    first 3 s). Invalid: nothing changes and the nearest valid cut points are suggested."""
    if edge not in ("start", "end") or not 0 <= index < len(segs):
        raise FeedbackError("nudge needs a segment index and edge 'start' or 'end'")
    if (delta is None) == (to is None):
        raise FeedbackError("give either delta or to")
    cur = list(segs)
    old = cur[index].start_s if edge == "start" else cur[index].end_s
    target = round(float(to) if to is not None else old + float(delta or 0), 3)

    def with_edge(t: float) -> list[FSeg]:
        s = cur[index]
        moved = FSeg(episode=s.episode, start_s=t if edge == "start" else s.start_s,
                     end_s=t if edge == "end" else s.end_s)  # fmt: skip
        return [*cur[:index], moved, *cur[index + 1 :]]

    if target < 0:
        return NudgeResult(False, cur, ["would go before the start of the episode"])
    errors = _violations(show, with_edge(target))
    if not errors:
        return NudgeResult(True, with_edge(target))

    ep = show.episode(cur[index].episode)
    cands = sorted(
        {round(b.start_s if edge == "start" else b.end_s, 3) for b in (ep.beats if ep else [])}
    )
    valid = [t for t in cands if abs(t - old) > 1e-3 and not _violations(show, with_edge(t))]
    nearest = sorted(valid, key=lambda t: (abs(t - target), t))[:2]
    return NudgeResult(False, cur, _plain(errors), sorted(nearest))


def apply_nudge(entries: list[Entry], promo: PromoView, run: str, segs: list[FSeg]) -> list[Entry]:
    """Store the editor's cut for this promo (creating the entry if needed)."""
    e = find_entry(entries, promo)
    if e is None:
        e = Entry(promo_id=promo.id, run=run, original=list(promo.segs), segments=list(promo.segs))
        entries = [*entries, e]
    e.segments, e.run, e.updated_at = segs, run, _now()
    return [x for x in entries if x.verdict or x.note or x.nudged]


def reset_cut(entries: list[Entry], promo: PromoView) -> list[Entry]:
    e = find_entry(entries, promo)
    if e is not None:
        e.segments = list(e.original)
        e.updated_at = _now()
    return [x for x in entries if x.verdict or x.note or x.nudged]


# ---------------------------------------------------------------------------- evaluation


@dataclass
class RejectedRow:
    rank: int | None  # rank in the evaluated run, None if no current promo matches
    segments: list[FSeg]
    note: str


@dataclass
class FeedbackSummary:
    k: int
    n_entries: int
    n_approved: int
    n_would_post: int
    n_rejected: int
    top: int  # promos actually in the top-k
    top_positive: int
    top_rejected: int
    top_unreviewed: int
    precision_at_k: float  # positives / top; unreviewed count as misses
    precision_reviewed: float | None  # positives / (positives + rejected) within the top-k
    reviewed_total: int  # reviewed promos in the whole ranking
    rejected: list[RejectedRow]


def evaluate_feedback(
    entries: Sequence[Entry], promos: Sequence[Any], k: int = 10
) -> FeedbackSummary:
    views = promo_views(promos)
    top = views[:k]
    labels = [find_entry(entries, v) for v in top]
    pos = sum(1 for e in labels if e and e.verdict in POSITIVE)
    rej = sum(1 for e in labels if e and e.verdict == "reject")
    unrev = sum(1 for e in labels if not e or e.verdict is None)
    reviewed = sum(1 for v in views if (e := find_entry(entries, v)) and e.verdict)
    rejected = []
    for e in entries:
        if e.verdict != "reject":
            continue
        hit = next((v for v in views if find_entry([e], v)), None)
        rejected.append(RejectedRow(hit.rank if hit else None, e.original, e.note))
    rejected.sort(key=lambda r: (r.rank is None, r.rank or 0))
    return FeedbackSummary(
        k=k,
        n_entries=len([e for e in entries if e.verdict]),
        n_approved=sum(e.verdict == "approve" for e in entries),
        n_would_post=sum(e.verdict == "would_post" for e in entries),
        n_rejected=sum(e.verdict == "reject" for e in entries),
        top=len(top),
        top_positive=pos,
        top_rejected=rej,
        top_unreviewed=unrev,
        precision_at_k=round(pos / max(1, len(top)), 3),
        precision_reviewed=round(pos / (pos + rej), 3) if pos + rej else None,
        reviewed_total=reviewed,
        rejected=rejected,
    )


def summary_dict(s: FeedbackSummary) -> dict[str, Any]:
    return {
        "k": s.k, "entries": s.n_entries, "approved": s.n_approved, "would_post": s.n_would_post,
        "rejected": s.n_rejected, "top": s.top, "top_positive": s.top_positive,
        "top_rejected": s.top_rejected, "top_unreviewed": s.top_unreviewed,
        "precision_at_k": s.precision_at_k, "precision_reviewed": s.precision_reviewed,
        "reviewed_total": s.reviewed_total,
    }  # fmt: skip


def _cut(segs: Sequence[FSeg]) -> str:
    return " + ".join(
        f"ep{s.episode} {format_timecode(s.start_s)}-{format_timecode(s.end_s)}" for s in segs
    )


SECTION_TITLE = "## 8b. Editor feedback"


def render_section(s: FeedbackSummary | None, feedback_file: str = "editor_feedback.yaml") -> str:
    """Markdown for the quality report. Notes are the editor's own words, shown verbatim."""
    if s is None or (not s.n_entries and not s.rejected):
        return (
            f"{SECTION_TITLE}\n\n- No editor feedback yet. In `make ui`, press Approve / Would "
            f"post / Reject on promos; verdicts are saved to `{feedback_file}`, then refresh this "
            "report with `make feedback RUN=<run folder>` (or just re-run: it is cached).\n"
        )
    lines = [
        SECTION_TITLE,
        "",
        f"- Reviewed by the editor: {s.n_entries} promos ({s.n_approved} approved, "
        f"{s.n_would_post} would post, {s.n_rejected} rejected), {s.reviewed_total} of them in "
        "this ranking. Labels come from the editor, not from the tool or an AI.",
        f"- **precision@{s.k} = {s.precision_at_k:.2f}**: {s.top_positive} of the top {s.top} were "
        "approved or marked would-post. Unreviewed promos count as misses, so this is a lower "
        f"bound while {s.top_unreviewed} of the top {s.top} are still unreviewed.",
    ]
    if s.precision_reviewed is not None:
        lines.append(
            f"- Among only the reviewed top-{s.k} ({s.top_positive + s.top_rejected}): "
            f"precision = {s.precision_reviewed:.2f}, {s.top_rejected} rejected."
        )
    if s.rejected:
        lines += ["", "Rejected, and why (the editor's note):", ""]
        for r in s.rejected:
            where = f"#{r.rank}" if r.rank else "not in this run"
            why = re.sub(r"\s+", " ", r.note).strip() or "(no reason given)"
            lines.append(f"- {where} ({_cut(r.segments)}): {why}")
    return "\n".join(lines) + "\n"


def replace_section(report: str, section: str) -> str:
    """Put ``section`` in a quality report: replace an existing 8b, else insert before section 9."""
    lines = report.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(SECTION_TITLE)), None)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
        )
        return "\n".join([*lines[:start], *section.rstrip().splitlines(), "", *lines[end:]]) + "\n"
    nine = next((i for i, ln in enumerate(lines) if ln.startswith("## 9.")), None)
    if nine is None:
        return report.rstrip() + "\n\n" + section
    return "\n".join([*lines[:nine], *section.rstrip().splitlines(), "", *lines[nine:]]) + "\n"
