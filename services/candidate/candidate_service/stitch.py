"""Multi-segment candidates: a strong hook seed + a strong cliffhanger seed (+ optional middle).

Segments are in story order (episode, then time), at most 3, each >= min_segment_s, and the
total is the SUM of segment durations (timestamps are per-episode).
"""

from __future__ import annotations

from itertools import product

from promo_common.schemas import BeatType

from candidate_service.config import CandidateConfig, Lexicon
from candidate_service.context import EpCtx
from candidate_service.signals import (
    make_view,
    score_cliff,
    score_hook,
    score_intensity,
    speakers,
)
from candidate_service.windows import Built, Seg, evaluate

HOOK_LENS = (7.0, 12.0, 18.0)
CLIFF_LENS = (8.0, 14.0, 22.0)
MID_LENS = (6.0, 10.0)


def _nearest(values: list[float], target: float, lo: float, hi: float) -> float | None:
    pool = [v for v in values if lo <= v <= hi]
    return min(pool, key=lambda v: abs(v - target)) if pool else None


def _spaced_top(scored: list[tuple[float, float]], k: int, spacing: float) -> list[float]:
    kept: list[float] = []
    for _, t in sorted(scored, key=lambda x: (-x[0], x[1])):
        if all(abs(t - u) >= spacing for u in kept):
            kept.append(t)
            if len(kept) >= k:
                break
    return kept


def hook_seeds(ctx: EpCtx, lex: Lexicon, cfg: CandidateConfig) -> list[tuple[float, list[float]]]:
    scored = [(score_hook(ctx, s, lex, cfg).score, s) for s in ctx.valid_starts]
    scored = [x for x in scored if x[0] >= cfg.seed_min_hook]
    out = []
    for s in _spaced_top(scored, cfg.hook_seeds_per_episode, cfg.seed_spacing_s):
        ends = {
            e
            for t in HOOK_LENS
            if (e := _nearest(ctx.valid_ends, s + t, s + cfg.min_segment_s, s + 18.0)) is not None
        }
        if ends:
            out.append((s, sorted(ends)))
    return out


def cliff_seeds(ctx: EpCtx, lex: Lexicon, cfg: CandidateConfig) -> list[tuple[float, list[float]]]:
    scored: list[tuple[float, float]] = []
    for e in ctx.valid_ends:
        starts = [s for s in ctx.valid_starts if s <= e - 10.0]
        if not starts:
            continue
        sc = score_cliff(make_view(ctx, starts[-1], e), lex, cfg).score
        if sc >= cfg.seed_min_cliff:
            scored.append((sc, e))
    out = []
    for e in _spaced_top(scored, cfg.cliff_seeds_per_episode, cfg.seed_spacing_s):
        begins = {
            s
            for t in CLIFF_LENS
            if (s := _nearest(ctx.valid_starts, e - t, e - 24.0, e - cfg.min_segment_s)) is not None
        }
        if begins:
            out.append((e, sorted(begins)))
    return out


def mid_pool(ctx: EpCtx, lex: Lexicon, cfg: CandidateConfig) -> list[tuple[float, float, float]]:
    """Intense middle segments: (intensity, start, end)."""
    found: list[tuple[float, float, float]] = []
    for s in ctx.valid_starts:
        for t in MID_LENS:
            e = _nearest(ctx.valid_ends, s + t, s + cfg.min_segment_s, s + 12.0)
            if e is None:
                continue
            v = make_view(ctx, s, e)
            dlg = sum(b.type is BeatType.DIALOGUE for b in v.beats)
            if dlg >= 2:
                found.append((score_intensity([v], lex), s, e))
    found.sort(key=lambda x: (-x[0], x[1]))
    out: list[tuple[float, float, float]] = []
    for item in found:
        if all(abs(item[1] - o[1]) >= cfg.seed_spacing_s for o in out):
            out.append(item)
        if len(out) >= 6:
            break
    return out


def _ordered(a: Seg, b: Seg, gap: float) -> bool:
    return a[0] < b[0] or (a[0] == b[0] and b[1] >= a[2] + gap)


def build_stitched(
    ctxs: dict[int, EpCtx], lex: Lexicon, cfg: CandidateConfig
) -> tuple[list[Built], int]:
    """Return (kept stitched candidates, number generated)."""
    hooks = {n: hook_seeds(c, lex, cfg) for n, c in ctxs.items()}
    cliffs = {n: cliff_seeds(c, lex, cfg) for n, c in ctxs.items()}
    mids = {n: mid_pool(c, lex, cfg) for n, c in ctxs.items()}
    gap = cfg.min_gap_same_episode_s
    best: list[tuple[Built, tuple[int, float], tuple[int, float]]] = []
    generated = 0

    for a in sorted(ctxs):
        for b in sorted(ctxs):
            if not (0 <= b - a <= cfg.max_episode_gap):
                continue
            mid_segs: list[Seg] = [(a, ms, me) for _, ms, me in mids[a]]
            if b != a:
                mid_segs += [(b, ms, me) for _, ms, me in mids[b]]
            for hs, h_ends in hooks[a]:
                for ce, c_starts in cliffs[b]:
                    # one best 2-seg and one best 3-seg per (hook seed, cliff seed)
                    top: dict[int, Built] = {}
                    for he, cs in product(h_ends, c_starts):
                        h, c = (a, hs, he), (b, cs, ce)
                        if not _ordered(h, c, gap):
                            continue
                        if not _shares_speaker(ctxs, h, c):
                            continue
                        combos: list[list[Seg]] = [[h, c]]
                        for m in mid_segs:
                            if _ordered(h, m, gap) and _ordered(m, c, gap):
                                combos.append([h, m, c])
                        for segs in combos:
                            total = sum(e - s for _, s, e in segs)
                            if not (cfg.min_total_s <= total <= cfg.max_total_s):
                                continue
                            built = evaluate(segs, ctxs, lex, cfg)
                            generated += 1
                            n = len(segs)
                            if n not in top or built.score > top[n].score:
                                top[n] = built
                    best.extend((v, (a, hs), (b, ce)) for v in top.values())

    # diversity: each hook seed / cliff seed used at most twice, then global top-N
    best.sort(key=lambda t: -t[0].score)
    used_h: dict[tuple[int, float], int] = {}
    used_c: dict[tuple[int, float], int] = {}
    kept: list[Built] = []
    for built, hk, ck in best:
        if used_h.get(hk, 0) >= 2 or used_c.get(ck, 0) >= 2:
            continue
        used_h[hk] = used_h.get(hk, 0) + 1
        used_c[ck] = used_c.get(ck, 0) + 1
        kept.append(built)
        if len(kept) >= cfg.stitch_top_n_total * 2:  # headroom for recap dedupe
            break
    return kept, generated


def _shares_speaker(ctxs: dict[int, EpCtx], h: Seg, c: Seg) -> bool:
    sh = speakers([make_view(ctxs[h[0]], h[1], h[2])])
    sc = speakers([make_view(ctxs[c[0]], c[1], c[2])])
    return bool(sh & sc)
