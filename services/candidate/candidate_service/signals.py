"""Rule-based scoring of one or more segments. Deterministic, no LLM.

Every sub-score is 0-10. Camera beats never count as hook/cliffhanger CONTENT (they describe
shots, not story), but camera/sfx text may contribute cliffhanger CUES such as "cut to black".
"""

from __future__ import annotations

from dataclasses import dataclass

from promo_common.cuts import CONTENT_TYPES, EPS
from promo_common.schemas import Beat, BeatType, RuleSignals

from candidate_service.config import CandidateConfig, Lexicon
from candidate_service.context import EpCtx

_PUNCT_END = ("...", "…", "—", "--", "-")


@dataclass
class View:
    ctx: EpCtx
    start: float
    end: float
    beats: list[Beat]


def make_view(ctx: EpCtx, start: float, end: float) -> View:
    return View(ctx, start, end, ctx.beats_in(start, end))


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _hits(b: Beat, lex: Lexicon) -> list[str]:
    t = b.text
    out = [
        m.group(0).lower() for rx in (lex.action, lex.reveal, lex.betrayal) for m in rx.finditer(t)
    ]
    for rx in (*lex.threat, *lex.accusation):
        m = rx.search(t)
        if m:
            out.append(m.group(0).lower())
    return sorted(set(out))


# ---- hook -----------------------------------------------------------------------------
@dataclass
class HookResult:
    score: float
    beat_id: str | None
    terms: list[str]


def score_hook(ctx: EpCtx, start: float, lex: Lexicon, cfg: CandidateConfig) -> HookResult:
    hw = cfg.hook_window_s
    firsts = [b for b in ctx.beats_in(start, start + hw + 2 * EPS) if b.type in CONTENT_TYPES]
    if not firsts:
        return HookResult(0.0, None, [])
    terms: set[str] = set()
    best: tuple[float, Beat] | None = None
    dialogue_text = ""
    emo = 0
    covered: list[tuple[float, float]] = []
    for b in firsts:
        h = _hits(b, lex)
        terms.update(h)
        val = len(h) + ("?" in b.text or "!" in b.text) * 0.5
        if b.type is BeatType.DIALOGUE:
            dialogue_text += " " + b.text
            emo += any(lex.is_intense_tag(t) for t in b.emotion_tags)
            val += 0.5 * any(lex.is_intense_tag(t) for t in b.emotion_tags)
        if b.type in (BeatType.DIALOGUE, BeatType.ACTION):
            covered.append((max(b.start_s, start), min(b.end_s, start + hw)))
        if best is None or val > best[0]:
            best = (val, b)
    covered.sort()
    cov, cursor = 0.0, start
    for s, e in covered:
        s = max(s, cursor)
        if e > s:
            cov += e - s
            cursor = e
    d = firsts[0].start_s - start
    early = 1.0 if d <= 1.0 else _clip(1 - (d - 1.0) / (hw - 1.0 or 1.0))
    kw = _clip(len(terms) / 2)
    q = 1.0 if ("?" in dialogue_text or "!" in dialogue_text) else (0.5 if dialogue_text else 0.0)
    score = 10 * (0.35 * kw + 0.15 * q + 0.20 * _clip(emo) + 0.20 * _clip(cov / hw) + 0.10 * early)
    return HookResult(round(score, 3), best[1].id if best else None, sorted(terms))


# ---- cliffhanger ------------------------------------------------------------------------
@dataclass
class CliffResult:
    score: float
    beat_id: str | None
    cues: list[str]
    gap_s: float
    at_end: bool


def score_cliff(view: View, lex: Lexicon, cfg: CandidateConfig) -> CliffResult:
    ctx = view.ctx
    gap = max(0.0, ctx.ep_end - view.end)
    c_end = _clip(1 - gap / cfg.tail_window_s)

    story = [b for b in view.beats if b.type in (BeatType.DIALOGUE, BeatType.ACTION)]
    pool = story or [b for b in view.beats if b.type in CONTENT_TYPES]
    last = max(pool, key=lambda b: (b.end_s, b.start_s)) if pool else None
    c_q = c_threat = c_res = 0.0
    if last is not None:
        t = last.text.strip()
        if t.endswith("?"):
            c_q = 1.0
        elif t.endswith(_PUNCT_END):
            c_q = 0.8
        elif t.endswith("!"):
            c_q = 0.4
        if _hits(last, lex) and last.type is BeatType.DIALOGUE:
            c_threat = 0.8
        elif lex.ending.search(t):
            c_threat = 0.5 if last.type is BeatType.ACTION else 0.3
        recent = [b.text for b in story[-2:]]
        c_res = 1.0 if any(lex.resolution.search(x) for x in recent) else 0.0

    cues: set[str] = set()
    strong = weak = False
    for b in view.ctx.beats_in(max(view.start, view.end - cfg.cue_window_s), view.end + 1e-3):
        for m in lex.cue_strong.finditer(b.text):
            cues.add(m.group(0).lower())
            strong = True
        for m in lex.cue_weak.finditer(b.text):
            cues.add(m.group(0).lower())
            weak = True
    # a trailing camera beat that straddles the cut may carry the cue ("...abrupt cut to black")
    for b in ctx.beats_in(max(0.0, view.end - 6.0), view.end + 1e-3):
        if b.type is BeatType.CAMERA and b.end_s >= view.end - EPS:
            for m in lex.cue_strong.finditer(b.text):
                cues.add(m.group(0).lower())
                strong = True
    c_sfx = 1.0 if strong else (0.6 if weak else 0.0)

    # episode-end prior (weight configurable so it can be ablated): 0.25 by default, the same
    # weight as each text cue, instead of dominating the score
    score = 10 * _clip(
        cfg.episode_end_weight * c_end + 0.25 * c_q + 0.25 * c_sfx + 0.25 * c_threat - 0.30 * c_res
    )
    return CliffResult(round(score, 3), last.id if last else None, sorted(cues), gap, gap <= 0.25)


# ---- intensity / dead air / clarity --------------------------------------------------------
def score_intensity(views: list[View], lex: Lexicon) -> float:
    dlg = [b for v in views for b in v.beats if b.type is BeatType.DIALOGUE]
    acts = [b for v in views for b in v.beats if b.type in (BeatType.ACTION, BeatType.SOUND_FX)]
    if not dlg and not acts:
        return 0.0
    a = (
        sum(any(lex.is_intense_tag(t) for t in b.emotion_tags) for b in dlg) / len(dlg)
        if dlg
        else 0
    )
    e = sum("!" in b.text for b in dlg) / len(dlg) if dlg else 0
    verbs = {m.group(0).lower() for b in acts for m in lex.action.finditer(b.text)}
    return round(10 * (0.5 * a + 0.3 * e + 0.2 * _clip(len(verbs) / 3)), 3)


def max_dead_air(view: View) -> float:
    ivs = sorted(
        (max(b.start_s, view.start), min(b.end_s, view.end))
        for b in view.beats
        if b.type in CONTENT_TYPES
    )
    cursor, worst = view.start, 0.0
    for s, e in ivs:
        worst = max(worst, s - cursor)
        cursor = max(cursor, e)
    return max(worst, view.end - cursor)


def speakers(views: list[View]) -> set[str]:
    return {b.speaker for v in views for b in v.beats if b.type is BeatType.DIALOGUE and b.speaker}


def compute_signals(
    views: list[View], lex: Lexicon, cfg: CandidateConfig, stitched: bool = False
) -> tuple[RuleSignals, float]:
    first, last = views[0], views[-1]
    hook = score_hook(first.ctx, first.start, lex, cfg)
    cliff = score_cliff(last, lex, cfg)
    intensity = score_intensity(views, lex)
    dead = max(max_dead_air(v) for v in views)
    spk = speakers(views)
    risks: list[str] = []

    # recap: does the opening replay the end of the previous episode?
    recap = first.ctx.recap
    first_dlg = [b for b in first.beats if b.type is BeatType.DIALOGUE][:6]
    opens_recap = bool(recap) and (
        hook.beat_id in recap
        or (bool(first_dlg) and sum(b.id in recap for b in first_dlg) / len(first_dlg) >= 0.5)
    )
    cont_dep = first.ctx.continuity_dependent and first.start < 8.0
    at_scene_start = first.start - first.ctx.scene_start(first.start) <= 1.0

    clarity = 5.0 + 2.0 * at_scene_start + 1.0 * first.ctx.is_first
    clarity -= (
        3.0 * cont_dep + min(3.0, max(0, len(spk) - 3)) + (1.0 if dead > cfg.dead_air_s else 0)
    )
    cross_ep = len({v.ctx.ep.number for v in views}) > 1
    if stitched:
        clarity -= 1.0 + 1.0 * cross_ep
        risks.append("stitched_cross_episode" if cross_ep else "stitched")
    if not at_scene_start:
        risks.append("starts_mid_scene")
    if cont_dep:
        risks.append("continuity_dependent")
    if opens_recap:
        risks.append("opens_with_recap")
    if dead > cfg.dead_air_s:
        risks.append("dead_air")
    if len(spk) > 3:
        risks.append("many_speakers")
    if not cliff.at_end and cliff.score < 4:
        risks.append("weak_ending")

    sig = RuleSignals(
        hook=_clip(hook.score, 0, 10),
        cliffhanger=_clip(cliff.score, 0, 10),
        intensity=_clip(intensity, 0, 10),
        clarity=_clip(clarity, 0, 10),
        hook_beat_id=hook.beat_id,
        cliff_beat_id=cliff.beat_id,
        hook_terms=hook.terms,
        cliff_cues=cliff.cues,
        ends_at_episode_end=cliff.at_end,
        episode_end_gap_s=round(cliff.gap_s, 3),
        max_dead_air_s=round(dead, 3),
        n_speakers=len(spk),
        opens_with_recap=opens_recap,
        continuity_dependent=cont_dep,
        risks=risks,
    )
    w = cfg.weights
    score = w.hook * sig.hook + w.cliffhanger * sig.cliffhanger + w.intensity * sig.intensity
    score += w.clarity * sig.clarity
    if dead > cfg.dead_air_s:
        score -= min(2.0, (dead - cfg.dead_air_s) / 4)
    if stitched:
        score -= cfg.stitch_penalty
    return sig, round(_clip(score, 0, 10), 3)
