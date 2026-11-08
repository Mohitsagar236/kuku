"""Scorer: hook/cliffhanger/intensity (context-aware), blind clarity, stitched coherence.

* The LLM only ever names beats by short id; ids are validated against code-computed eligibility.
* Results are cached PER CANDIDATE (content hash of exactly what was sent), so re-runs resume
  with zero re-billing and the ablation run re-pays only for candidates whose text changed.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from pydantic import ValidationError

from promo_finder.domain.models import Candidate, LLMScores, Show
from promo_finder.infrastructure.llm import LLMRequest, ManagedLLM, load_prompt
from promo_finder.infrastructure.llm.cache import make_key
from promo_finder.scoring.batch import Job, run_batch
from promo_finder.scoring.config import ScoringConfig
from promo_finder.scoring.ids import CandView, IdMap, build_view
from promo_finder.scoring.schemas import CoherenceItem, RefineItem, ScoreItem, cap_words
from promo_finder.scoring.text import (
    beat_line,
    blind_text,
    candidate_header,
    make_redactor,
    refine_text,
)

log = logging.getLogger(__name__)


Shortlist = Callable[[list[Candidate], dict[str, LLMScores]], list[str]]


def _chunks(seq: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _stable_order(ids: Iterable[str], salt: str = "") -> list[str]:
    return sorted(ids, key=lambda i: hashlib.sha256((salt + i).encode()).hexdigest())


class Scorer:
    def __init__(
        self,
        llm: ManagedLLM,
        show: Show,
        cfg: ScoringConfig,
        redact_terms: Iterable[str] = (),
        namespace: str = "",
        workers: int = 1,
    ) -> None:
        self.llm, self.show, self.cfg, self.ns, self.workers = llm, show, cfg, namespace, workers
        self.ids = IdMap.build(show)
        self.red = make_redactor(list(redact_terms))
        self.p_score = load_prompt(cfg.score_prompt)
        self.p_clarity = load_prompt(cfg.clarity_prompt)
        self.p_coh = load_prompt(cfg.coherence_prompt)
        self.stats: Counter[str] = Counter()
        self._lock = threading.Lock()
        self.bypass_cache = False
        self.offline = False  # cache-only: never call the LLM (partial runs after quota stops)

    # -- plumbing -----------------------------------------------------------------------
    def prompt_versions(self) -> dict[str, str]:
        return {p.name: p.sha for p in (self.p_score, self.p_clarity, self.p_coh)}

    def _key(self, task: str, prompt_sha: str, *blobs: str) -> str:
        return make_key(
            task, prompt_sha, self.llm.model, str(self.llm.temperature), self.ns, *blobs
        )

    def _cached(self, task: str, key: str) -> dict[str, Any] | None:
        if self.bypass_cache:
            return None
        hit = self.llm.cache.get(key)
        if hit is not None:
            self.llm.note_cache_hit(task)
            with self._lock:
                self.stats[f"{task}_cached"] += 1
        return hit

    def _bump(self, key: str) -> None:
        with self._lock:
            self.stats[key] += 1

    def _parallel(self, fns: list[Callable[[], None]]) -> None:
        if self.workers <= 1 or len(fns) <= 1:
            for f in fns:
                f()
            return
        ex = ThreadPoolExecutor(self.workers)
        try:
            for fut in as_completed([ex.submit(f) for f in fns]):
                fut.result()
        except BaseException:
            ex.shutdown(wait=True, cancel_futures=True)
            raise
        ex.shutdown()

    def _context(self, jobs: list[Job]) -> str:
        lines = []
        for n in sorted({sv.episode for j in jobs for sv in j.view.segs}):
            ep = self.show.episode(n)
            assert ep is not None
            extra = f" Continuity: {ep.continuity_notes}" if ep.continuity_notes else ""
            lines.append(f"Episode {n}: {self.red(ep.logline)}{self.red(extra)}")
        return "\n".join(lines)

    def _view(self, c: Candidate) -> CandView:
        k = self.cfg
        return build_view(
            c, self.show, self.ids, k.hook_window_s, k.cliff_window_s,
            k.min_total_s, k.max_total_s, k.end_spacing_s, refine=k.refine_ends,
        )  # fmt: skip

    # -- main judge ---------------------------------------------------------------------
    def _score_key(self, v: CandView) -> str:
        body = candidate_header("c", v, self.ids) + "\n".join(
            beat_line(b, self.ids, self.red) for sv in v.segs for b in sv.beats
        )
        ctx = self._context([Job(v.cand.id, v, "")])
        return self._key("score", self.p_score.sha, body, ctx)

    def _score_render(self, jobs: list[Job], labels: dict[str, str], note: str) -> LLMRequest:
        beats = {}
        for j in jobs:
            for sv in j.view.segs:
                for b in sv.beats:
                    beats[(sv.episode, b.start_s, b.id)] = beat_line(b, self.ids, self.red)
        beat_text = "\n".join(beats[k] for k in sorted(beats))
        cands = "\n".join(candidate_header(labels[j.cand_id], j.view, self.ids) for j in jobs)
        system, user = self.p_score.render(
            context=self._context(jobs), beats=beat_text, candidates=cands, retry_note=note
        )
        meta = {
            "candidates": [
                {
                    "label": labels[j.cand_id],
                    "beats": sorted(j.view.all_ids),
                    "hook_ok": j.view.hook_ok,
                    "cliff_ok": j.view.cliff_ok,
                    "texts": {
                        self.ids.to_short[b.id]: b.text for sv in j.view.segs for b in sv.beats
                    },
                }
                for j in jobs
            ]
        }
        return LLMRequest(system, user, "score", 200 * len(jobs) + 300, meta=meta)

    def _score_validate(
        self, row: dict[str, Any], job: Job
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            item = ScoreItem(**row)
        except (ValidationError, TypeError) as e:
            return None, f"schema: {str(e).splitlines()[0][:80]}"
        v = job.view
        if item.hook not in v.hook_ok:
            return None, f"hook id {item.hook} is not hook-eligible (unknown, camera, or after 3s)"
        if item.cliff not in v.cliff_ok:
            return None, f"cliff id {item.cliff} is not cliff-eligible"
        for name, tid in (("ts", item.ts), ("te", item.te)):
            if tid is not None and tid not in v.all_ids:
                return None, f"{name} id {tid} is not a beat of this candidate"
        d = item.model_dump()
        d["why"] = cap_words(item.why, self.cfg.max_words_why)
        return d, None

    # -- blind clarity / coherence ---------------------------------------------------------
    def _blind_render(
        self, task: str, prompt: Any, jobs: list[Job], labels: dict[str, str], note: str
    ) -> LLMRequest:
        blocks = "\n\n".join(
            f"=== {labels[j.cand_id]} ===\n{blind_text(j.view, self.red)}" for j in jobs
        )
        system, user = prompt.render(candidates=blocks, retry_note=note)
        meta = {
            "candidates": [
                {"label": labels[j.cand_id], "text": blind_text(j.view, self.red)} for j in jobs
            ]
        }
        return LLMRequest(system, user, task, 60 * len(jobs) + 150, meta=meta)

    def _refine_blob(self, v: CandView) -> str:
        """Exactly what the stranger sees for this clip (also the cache key material)."""
        if v.end_options:
            return refine_text(v, self.ids, self.show, self.red)
        return blind_text(v, self.red)

    def _refine_render(self, jobs: list[Job], labels: dict[str, str], note: str) -> LLMRequest:
        blocks, cands_meta = [], []
        for j in jobs:
            v, lab = j.view, labels[j.cand_id]
            kind = "" if v.end_options else " (stitched)" if v.cand.kind == "stitched" else ""
            blob = self._refine_blob(v)
            blocks.append(f"=== {lab}{kind} ===\n{blob}")
            cands_meta.append({
                "label": lab, "text": blob, "end_options": list(v.end_options),
                "current_end": v.current_end,
                "end_texts": {self.ids.to_short[b.id]: b.text for b in
                              (self.ids.to_beat[o] for o in v.end_options)},
            })  # fmt: skip
        system, user = self.p_clarity.render(candidates="\n\n".join(blocks), retry_note=note)
        return LLMRequest(
            system, user, "clarity", 70 * len(jobs) + 200, meta={"candidates": cands_meta}
        )

    def _refine_validate(
        self, row: dict[str, Any], job: Job
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            item = RefineItem(**row)
        except (ValidationError, TypeError) as e:
            return None, f"schema: {str(e).splitlines()[0][:80]}"
        d = item.model_dump()
        d["gap"] = cap_words(item.gap, self.cfg.max_words_gap)
        opts = job.view.end_options
        if opts:
            end = (item.end or "").strip().lower()
            if end not in opts:
                return None, f"end id {item.end} is not one of this clip's legal end points"
            if item.cliff is None:
                return None, "cliff score missing"
            d["end"] = end
        else:
            d["end"], d["cliff"] = None, None
        return d, None

    def _blind_validate(self, model: type[CoherenceItem]) -> Callable[..., Any]:
        def validate(row: dict[str, Any], job: Job) -> tuple[dict[str, Any] | None, str | None]:
            try:
                item = model(**row)
            except (ValidationError, TypeError) as e:
                return None, f"schema: {str(e).splitlines()[0][:80]}"
            d = item.model_dump()
            d["gap"] = cap_words(item.gap, self.cfg.max_words_gap)
            return d, None

        return validate

    # -- orchestration --------------------------------------------------------------------
    def _judge(
        self,
        task: str,
        jobs: list[Job],
        batches: list[list[Job]],
        render: Callable[..., LLMRequest],
        validate: Callable[..., Any],
        sink: dict[str, dict[str, Any]],
        errs: dict[str, list[str]],
    ) -> None:
        def run(batch: list[Job]) -> None:
            if self.offline:
                with self._lock:
                    for j in batch:
                        errs.setdefault(j.cand_id, []).append(f"{task}: not scored (offline)")
                return
            ok, bad = run_batch(
                self.llm, batch, render, validate, self.cfg.max_validation_retries, self._bump, task
            )
            for cid, res in ok.items():
                key = next(j.key for j in batch if j.cand_id == cid)
                self.llm.cache.set(key, {"result": res})
                sink[cid] = res
            with self._lock:
                self.stats[f"{task}_live"] += len(batch)
                self.stats[f"{task}_failed"] += len(bad)
                for cid, e in bad.items():
                    errs.setdefault(cid, []).append(f"{task}: {e}")

        self._parallel([lambda b=b: run(b) for b in batches])  # type: ignore[misc]

    def score(
        self,
        cands: list[Candidate],
        bypass_cache: bool = False,
        shortlist: Shortlist | None = None,
    ) -> dict[str, LLMScores]:
        """Two-stage cascade when ``shortlist`` is given.

        Stage 1 (hook/cliffhanger/intensity/spoiler) runs on every candidate. ``shortlist`` then
        picks which candidates deserve stage 2 (blind clarity + stitched coherence), which is the
        expensive-per-candidate part. Candidates outside it get ``stage2=False``.
        """
        self.bypass_cache = bypass_cache
        views = {c.id: self._view(c) for c in cands}
        errs: dict[str, list[str]] = {}
        main: dict[str, dict[str, Any]] = {}
        clar: dict[str, dict[str, Any]] = {}
        coh: dict[str, dict[str, Any]] = {}

        # 1. context-aware judge: singles batched per episode, stitched in small batches
        todo: list[Job] = []
        for cid, v in views.items():
            key = self._score_key(v)
            hit = self._cached("score", key)
            if hit:
                main[cid] = hit["result"]
            else:
                todo.append(Job(cid, v, key))
        by_ep: dict[int, list[Job]] = {}
        stitched: list[Job] = []
        for j in todo:
            (
                by_ep.setdefault(j.view.segs[0].episode, [])
                if j.view.cand.kind == "single"
                else stitched
            ).append(j)
        batches = [b for jobs in by_ep.values() for b in _chunks(jobs, self.cfg.max_batch)]
        batches += list(_chunks(stitched, self.cfg.stitched_batch))
        self._judge("score", todo, batches, self._score_render, self._score_validate, main, errs)

        # cascade: only the shortlist gets the blind judges
        stage2_ids = set(views)
        if shortlist is not None:
            interim = {
                c.id: self._assemble(c, main.get(c.id), None, None, errs.get(c.id, []))
                for c in cands
            }
            stage2_ids = set(shortlist(cands, interim))
            self.stats["stage2_candidates"] = len(stage2_ids)
        views2 = {cid: v for cid, v in views.items() if cid in stage2_ids}

        # 2. blind clarity: mixed across episodes
        todo = []
        for cid in _stable_order(views2):
            v = views[cid]
            key = self._key("clarity", self.p_clarity.sha, self._refine_blob(v))
            hit = self._cached("clarity", key)
            if hit:
                clar[cid] = hit["result"]
            else:
                todo.append(Job(cid, v, key))
        self._judge(
            "clarity",
            todo,
            list(_chunks(todo, self.cfg.clarity_batch)),
            self._refine_render,
            self._refine_validate,
            clar,
            errs,
        )

        # 3. stitched coherence
        todo = []
        for cid in _stable_order(c.id for c in cands if c.kind == "stitched" and c.id in views2):
            v = views[cid]
            key = self._key("coherence", self.p_coh.sha, blind_text(v, self.red))
            hit = self._cached("coherence", key)
            if hit:
                coh[cid] = hit["result"]
            else:
                todo.append(Job(cid, v, key))
        render_h = lambda j, lb, n: self._blind_render("coherence", self.p_coh, j, lb, n)  # noqa: E731
        self._judge("coherence", todo, list(_chunks(todo, self.cfg.stitched_batch)), render_h,
                    self._blind_validate(CoherenceItem), coh, errs)  # fmt: skip

        return {
            c.id: self._assemble(
                c, main.get(c.id), clar.get(c.id), coh.get(c.id), errs.get(c.id, []),
                stage2=c.id in stage2_ids,
            )
            for c in cands
        }  # fmt: skip

    def _assemble(
        self,
        c: Candidate,
        m: dict[str, Any] | None,
        cl: dict[str, Any] | None,
        co: dict[str, Any] | None,
        errors: list[str],
        stage2: bool = True,
    ) -> LLMScores:
        def full(sid: str | None) -> str | None:
            return self.ids.to_beat[sid].id if sid else None

        s = LLMScores(errors=errors, stage2=stage2)
        if m:
            s.main_ok = True
            s.hook, s.cliffhanger, s.intensity, s.spoiler_risk = m["h"], m["c"], m["i"], m["s"]
            s.hook_beat_id, s.cliff_beat_id = full(m["hook"]), full(m["cliff"])
            s.trim_start_beat_id, s.trim_end_beat_id = full(m.get("ts")), full(m.get("te"))
            s.why = m.get("why", "")
        if cl:
            s.clarity_ok, s.clarity, s.clarity_gap = True, cl["clarity"], cl.get("gap", "")
            if cl.get("end"):
                beat = self.ids.to_beat[cl["end"]]
                s.end_beat_id, s.end_cliff = beat.id, cl.get("cliff")
                s.end_changed = abs(beat.end_s - c.segments[0].end_s) > 1e-3
        if c.kind == "stitched":
            s.coherence_ok = bool(co)
            if co:
                s.coherence, s.coherence_gap = co["coherence"], co.get("gap", "")
        return s

    def digest(self, cand: Candidate, llm: LLMScores | None) -> str:
        """Compact text for the cross-show rerank: opening, ending and the one-line note."""
        v = self._view(cand)

        def short(b: Any) -> str:
            who = f"{b.speaker}: " if b.speaker else ""
            return (who + self.red(b.text))[:140]

        opening = [
            b for b in v.segs[0].beats if b.start_s <= v.segs[0].start + self.cfg.hook_window_s
        ][:3]
        ending = v.segs[-1].beats[-2:]
        note = f" NOTE: {llm.why}" if llm and llm.why else ""
        return (
            f"{cand.kind}, {cand.duration_s:.0f}s. "
            f"OPENING: {' / '.join(short(b) for b in opening)} "
            f"ENDING: {' / '.join(short(b) for b in ending)}.{note}"
        )
