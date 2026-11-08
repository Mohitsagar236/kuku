"""Single-segment candidate windows: enumerate, score, suppress near-duplicates."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from promo_finder.candidates.config import CandidateConfig, Lexicon
from promo_finder.candidates.context import EpCtx
from promo_finder.candidates.signals import compute_signals, make_view
from promo_finder.domain.models import RuleSignals
from promo_finder.domain.timecode import iou

Seg = tuple[int, float, float]  # (episode_number, start_s, end_s)


@dataclass
class Built:
    kind: str  # single | stitched
    segs: list[Seg]
    signals: RuleSignals
    score: float

    @property
    def duration(self) -> float:
        return sum(e - s for _, s, e in self.segs)

    @property
    def episodes(self) -> set[int]:
        return {ep for ep, _, _ in self.segs}


def evaluate(segs: list[Seg], ctxs: dict[int, EpCtx], lex: Lexicon, cfg: CandidateConfig) -> Built:
    views = [make_view(ctxs[ep], s, e) for ep, s, e in segs]
    stitched = len(segs) > 1
    sig, score = compute_signals(views, lex, cfg, stitched=stitched)
    return Built("stitched" if stitched else "single", segs, sig, score)


def enumerate_windows(ctx: EpCtx, lex: Lexicon, cfg: CandidateConfig) -> list[Built]:
    """Every valid [S, E] with S, E on beat boundaries, not inside dialogue/action, 20-60 s."""
    out: list[Built] = []
    ends = ctx.valid_ends
    ep = ctx.ep.number
    for s in ctx.valid_starts:
        lo = bisect_left(ends, s + cfg.min_total_s - 1e-6)
        hi = bisect_right(ends, s + cfg.max_total_s + 1e-6)
        for e in ends[lo:hi]:
            out.append(evaluate([(ep, s, e)], {ep: ctx}, lex, cfg))
    return out


def nms(wins: list[Built], cfg: CandidateConfig, limit: int) -> list[Built]:
    """Greedy: best first; drop windows that overlap a kept one (IoU) or share its start/end."""
    kept: list[Built] = []
    for w in sorted(wins, key=lambda w: (-w.score, w.segs[0][1], w.segs[0][2])):
        _, s, e = w.segs[0]
        clash = any(
            iou(s, e, ks, ke) > cfg.nms_iou
            or abs(s - ks) < cfg.nms_edge_tol_s
            or abs(e - ke) < cfg.nms_edge_tol_s
            for _, ks, ke in (k.segs[0] for k in kept)
        )
        if not clash:
            kept.append(w)
            if len(kept) >= limit:
                break
    return kept
