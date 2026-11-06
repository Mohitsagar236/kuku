"""Orchestrates candidate generation for a whole show. Logs counts only, never script text."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from promo_common.schemas import BeatType, Candidate, Segment, Show

from candidate_service.config import CandidateConfig, Lexicon
from candidate_service.context import EpCtx
from candidate_service.recap import detect_recap, normalize_line
from candidate_service.stitch import build_stitched
from candidate_service.windows import Built, enumerate_windows, nms

log = logging.getLogger(__name__)


@dataclass
class EpisodeStats:
    episode: int
    duration_s: float
    beats: int
    valid_starts: int
    valid_ends: int
    windows: int = 0
    after_nms: int = 0
    kept: int = 0
    recap_beats: int = 0


@dataclass
class GenStats:
    episodes: list[EpisodeStats] = field(default_factory=list)
    stitched_generated: int = 0
    stitched_pool: int = 0
    stitched_kept: int = 0
    recap_duplicates_removed: int = 0

    @property
    def total_windows(self) -> int:
        return sum(e.windows for e in self.episodes)

    @property
    def total_kept(self) -> int:
        return sum(e.kept for e in self.episodes) + self.stitched_kept


def build_contexts(show: Show, cfg: CandidateConfig) -> dict[int, EpCtx]:
    ctxs: dict[int, EpCtx] = {}
    prev = None
    for i, ep in enumerate(show.episodes):
        c = EpCtx(ep, cfg, is_first=(i == 0))
        c.recap = detect_recap(prev, ep, cfg)
        ctxs[ep.number] = c
        prev = ep
    return ctxs


Fingerprint = dict[str, frozenset[tuple[int, str]]]  # canonical line -> source (episode, beat id)


def _fingerprint(b: Built, ctxs: dict[int, EpCtx], min_words: int) -> Fingerprint:
    lines: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for ep, s, e in b.segs:
        ctx = ctxs[ep]
        for beat in ctx.beats_in(s, e):
            if beat.type is not BeatType.DIALOGUE:
                continue
            key = ctx.recap.get(beat.id) or normalize_line(beat.text)
            if len(key.split()) >= min_words:
                lines[key].add((ep, beat.id))
    return {k: frozenset(v) for k, v in lines.items()}


def _replayed(a: Fingerprint, b: Fingerprint) -> int:
    """Lines present in both candidates but coming from DIFFERENT source beats (a replay).

    Two candidates that merely reuse the same beats are not duplicates of each other.
    """
    return sum(1 for k in a.keys() & b.keys() if not (a[k] & b[k]))


def dedupe_cross_episode(
    pool: list[Built], ctxs: dict[int, EpCtx], cfg: CandidateConfig
) -> tuple[list[Built], dict[int, list[Built]]]:
    """Drop candidates that replay another episode's content (recap). Best score wins.

    Returns (survivors, {survivor index: dropped candidates it replaced}).
    """
    order = sorted(range(len(pool)), key=lambda i: (-pool[i].score, min(pool[i].episodes)))
    fps = {i: _fingerprint(pool[i], ctxs, cfg.recap_min_words) for i in order}
    index: dict[str, list[int]] = defaultdict(list)
    kept: list[int] = []
    dropped_by: dict[int, list[Built]] = defaultdict(list)
    for i in order:
        fp = fps[i]
        dup_of: int | None = None
        seen = {j for line in fp for j in index.get(line, [])}
        for j in seen:
            shared = _replayed(fp, fps[j])
            if shared >= 2 and shared / min(len(fp), len(fps[j])) >= cfg.recap_overlap:
                dup_of = j
                break
        if dup_of is not None:
            dropped_by[dup_of].append(pool[i])
            continue
        kept.append(i)
        for line in fp:
            index[line].append(i)
    pos = {j: n for n, j in enumerate(kept)}
    return [pool[i] for i in kept], {pos[j]: v for j, v in dropped_by.items()}


def _to_candidate(b: Built, ctxs: dict[int, EpCtx], dup_ids: list[str]) -> Candidate:
    segments = []
    h = hashlib.sha256()
    for ep, s, e in b.segs:
        ctx = ctxs[ep]
        sid, eid = ctx.boundary_ids(s, e)
        segments.append(
            Segment(episode_number=ep, start_s=s, end_s=e, start_beat_id=sid, end_beat_id=eid)
        )
        for beat in ctx.beats_in(s, e):
            h.update(f"{beat.id}|{beat.type.value}|{beat.speaker}|{beat.text}\n".encode())
    cid = hashlib.sha1(
        "|".join(f"{ep}:{s:.2f}-{e:.2f}" for ep, s, e in b.segs).encode()
    ).hexdigest()[:12]
    return Candidate(
        id=cid,
        kind="stitched" if len(b.segs) > 1 else "single",
        segments=segments,
        duration_s=round(b.duration, 3),
        rule_score=b.score,
        signals=b.signals,
        content_hash=h.hexdigest(),
        duplicate_of=dup_ids,
    )


def generate_candidates(
    show: Show, cfg: CandidateConfig, lex: Lexicon
) -> tuple[list[Candidate], GenStats]:
    ctxs = build_contexts(show, cfg)
    stats = GenStats()
    pool: list[Built] = []
    per_ep: dict[int, EpisodeStats] = {}

    for n, ctx in ctxs.items():
        wins = enumerate_windows(ctx, lex, cfg)
        pooled = nms(wins, cfg, cfg.pool_per_episode)
        es = EpisodeStats(
            n, ctx.ep.duration_s, len(ctx.beats), len(ctx.valid_starts), len(ctx.valid_ends),
            windows=len(wins), after_nms=len(pooled), recap_beats=len(ctx.recap),
        )  # fmt: skip
        stats.episodes.append(es)
        per_ep[n] = es
        pool.extend(pooled)

    stitched, generated = build_stitched(ctxs, lex, cfg)
    stats.stitched_generated, stats.stitched_pool = generated, len(stitched)
    pool.extend(stitched)

    n_before = len(pool)
    survivors, dropped = dedupe_cross_episode(pool, ctxs, cfg)
    stats.recap_duplicates_removed = n_before - len(survivors)

    singles: dict[int, list[tuple[Built, list[Built]]]] = defaultdict(list)
    multi: list[tuple[Built, list[Built]]] = []
    for i, b in enumerate(survivors):
        item = (b, dropped.get(i, []))
        if b.kind == "single":
            singles[b.segs[0][0]].append(item)
        else:
            multi.append(item)

    chosen: list[tuple[Built, list[Built]]] = []
    for n, items in singles.items():
        items.sort(key=lambda t: -t[0].score)
        top = items[: cfg.top_n_per_episode]
        per_ep[n].kept = len(top)
        chosen.extend(top)
    multi.sort(key=lambda t: -t[0].score)
    multi = multi[: cfg.stitch_top_n_total]
    stats.stitched_kept = len(multi)
    chosen.extend(multi)

    out = []
    for b, dups in chosen:
        dup_ids = [_to_candidate(d, ctxs, []).id for d in dups]
        out.append(_to_candidate(b, ctxs, dup_ids))
    out.sort(key=lambda c: -c.rule_score)
    log.info(
        "candidates_generated",
        extra={"candidates": len(out), "windows": stats.total_windows,
               "recap_removed": stats.recap_duplicates_removed},
    )  # fmt: skip
    return out, stats
