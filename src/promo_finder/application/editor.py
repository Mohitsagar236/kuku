"""Editor review and cut adjustment use cases; independent of HTTP."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from promo_finder.domain.models import Show
from promo_finder.domain.timecode import format_timecode
from promo_finder.evaluation import feedback as fb
from promo_finder.infrastructure.locking import directory_lock
from promo_finder.infrastructure.runs import RunRepository


class EditorError(Exception):
    def __init__(self, code: int, message: str, **extra: object) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


class EditorService:
    def __init__(self, runs: RunRepository) -> None:
        self.runs = runs
        self._shows_lock = threading.Lock()
        self._feedback_lock = threading.RLock()

    # ----------------------------------------------------------------------- editor feedback

    @staticmethod
    def feedback_file(d: Path) -> Path:
        """Always next to the run folders of the same show: derived, never taken from a request."""
        path = d.parent / "editor_feedback.yaml"
        if path.is_symlink():
            raise EditorError(409, "feedback file must not be a symbolic link")
        return path

    def get_show(self, d: Path) -> tuple[Show | None, str]:
        """The screenplay timeline this run was made from, or (None, why nudging is unavailable)."""
        meta_file = d / "run_metadata.json"
        if not meta_file.is_file() or meta_file.is_symlink():
            return None, "This run has no run_metadata.json (re-run it to enable cut nudges)."
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, ValueError):
            return None, "run_metadata.json is unreadable."
        src = meta.get("source_dir")
        if not src:
            return None, "This run predates cut nudges: re-run it (cached, no new LLM calls)."
        if not Path(src).is_dir():
            return None, f"The screenplay folder '{Path(src).name}' was not found."
        eps = meta.get("episodes_requested")
        want = frozenset(eps) if isinstance(eps, list) else None
        key = (src, meta.get("show", {}).get("source_hash"), want)
        with self._shows_lock:
            try:
                from promo_finder.ingestion import load_show

                show = load_show(src, episodes=set(want) if want else None)
            except Exception as e:  # unreadable folder: nudges off, reviews still work
                return None, f"The screenplay could not be re-read ({type(e).__name__})."
            if show.source_hash != key[1]:
                return None, "The screenplay folder has changed since this run (re-run it)."
            return show, ""

    @staticmethod
    def _seg_json(s: fb.FSeg) -> dict[str, Any]:
        return {
            "episode": s.episode,
            "start_s": s.start_s,
            "end_s": s.end_s,
            "start_ts": format_timecode(s.start_s),
            "end_ts": format_timecode(s.end_s),
        }

    @staticmethod
    def _entry_json(e: fb.Entry) -> dict[str, Any]:
        return {
            "verdict": e.verdict,
            "note": e.note,
            "nudged": e.nudged,
            "segments": [EditorService._seg_json(s) for s in e.segments],
            "original": [EditorService._seg_json(s) for s in e.original],
        }

    def feedback_state(self, run_id: str) -> dict[str, Any]:
        d = self.runs.run_dir(run_id)
        if d is None:
            raise EditorError(404, "run not found")
        try:
            entries = fb.load_feedback(self.feedback_file(d))
        except fb.FeedbackError as e:
            raise EditorError(409, f"{e}. Fix or delete the file; nothing was changed.") from e
        promos = json.loads((d / "promos.json").read_text())["promos"]
        views = fb.promo_views(promos)
        mine: dict[str, Any] = {}
        for v in views:
            entry = fb.find_entry(entries, v)
            if entry is not None:
                mine[v.id] = self._entry_json(entry)
        _, why = self.get_show(d)
        return {
            "entries": mine,
            "summary": fb.summary_dict(fb.evaluate_feedback(entries, promos, 10)),
            "can_nudge": not why,
            "nudge_reason": why,
            "file": f"outputs/{d.parent.name}/editor_feedback.yaml",
            "refresh_command": f"make feedback RUN=outputs/{run_id}",
        }

    def _view_of(self, d: Path, promo_id: object) -> fb.PromoView:
        promos = json.loads((d / "promos.json").read_text())["promos"]
        hit = next((v for v in fb.promo_views(promos) if v.id == promo_id), None)
        if hit is None:
            raise EditorError(404, "that promo is not in this run's main ranking")
        return hit

    @staticmethod
    def _check(body: object, allowed: set[str], required: set[str]) -> dict[str, Any]:
        if not isinstance(body, dict) or not required <= set(body) or not set(body) <= allowed:
            raise EditorError(
                400,
                f"required fields: {sorted(required)}; allowed fields: {sorted(allowed)}",
            )
        return body

    def post_review(self, run_id: str, body: object) -> dict[str, Any]:
        d = self.runs.run_dir(run_id)
        if d is None:
            raise EditorError(404, "run not found")
        b = self._check(body, {"promo_id", "verdict", "note"}, {"promo_id"})
        if "verdict" not in b and "note" not in b:
            raise EditorError(400, "give a verdict and/or a note")
        for k in ("verdict", "note"):
            if k in b and not isinstance(b[k], str):
                raise EditorError(400, f"{k} must be a string")
        view = self._view_of(d, b["promo_id"])
        with self._feedback_lock, directory_lock(d.parent):
            try:
                entries = fb.load_feedback(self.feedback_file(d))
                entries = fb.set_review(entries, view, run_id, b.get("verdict"), b.get("note"))
            except fb.FeedbackError as e:
                raise EditorError(409 if "malformed" in str(e) else 400, str(e)) from e
            fb.save_feedback(self.feedback_file(d), d.parent.name, entries)
        return self.feedback_state(run_id)

    def post_nudge(self, run_id: str, body: object) -> dict[str, Any]:
        d = self.runs.run_dir(run_id)
        if d is None:
            raise EditorError(404, "run not found")
        b = self._check(body, {"promo_id", "segment", "edge", "delta", "to", "reset"}, {"promo_id"})
        view = self._view_of(d, b["promo_id"])
        with self._feedback_lock, directory_lock(d.parent):
            try:
                entries = fb.load_feedback(self.feedback_file(d))
                if b.get("reset") is True:
                    entries = fb.reset_cut(entries, view)
                else:
                    show, why = self.get_show(d)
                    if show is None:
                        raise EditorError(409, why)
                    idx, edge = b.get("segment"), b.get("edge")
                    nums = [b.get(k) for k in ("delta", "to") if b.get(k) is not None]
                    if (
                        not isinstance(idx, int)
                        or isinstance(idx, bool)
                        or edge not in ("start", "end")
                    ):
                        raise EditorError(
                            400, "segment must be an integer and edge 'start' or 'end'"
                        )
                    if (
                        len(nums) != 1
                        or isinstance(nums[0], bool)
                        or not isinstance(nums[0], int | float)
                    ):
                        raise EditorError(400, "give exactly one number: delta or to")
                    cur = fb.find_entry(entries, view)
                    segs = list(cur.segments) if cur else list(view.segs)
                    res = fb.nudge(show, segs, idx, edge, b.get("delta"), b.get("to"))
                    if not res.ok:
                        raise EditorError(
                            422,
                            "That cut point is not allowed: " + "; ".join(res.reasons) + ".",
                            reasons=res.reasons,
                            suggestions=[
                                {"t": t, "ts": format_timecode(t)} for t in res.suggestions
                            ],
                        )
                    entries = fb.apply_nudge(entries, view, run_id, res.segments)
            except fb.FeedbackError as e:
                raise EditorError(409 if "malformed" in str(e) else 400, str(e)) from e
            fb.save_feedback(self.feedback_file(d), d.parent.name, entries)
        return self.feedback_state(run_id)
