"""Trim, final validation and construction of the delivered Promo records."""

from __future__ import annotations

from dataclasses import dataclass, field

from promo_common.schemas import Beat, Moment, Promo, PromoSegment, Segment, Show, SubScores
from promo_common.timecode import format_timecode
from promo_common.validation import validate_segments

from ranking_service.fusion import Fused

CLIFF_WINDOW_S = 8.0
HOOK_WINDOW_S = 3.0
_PHRASE = {
    "hook": "opens with a strong hook ({v:.0f}/10)",
    "cliffhanger": "ends on an unresolved cliffhanger ({v:.0f}/10)",
    "clarity": "makes sense to a newcomer ({v:.0f}/10)",
    "intensity": "carries high emotional intensity ({v:.0f}/10)",
}


@dataclass
class FinalizeStats:
    validation_failures: list[tuple[str, list[str]]] = field(default_factory=list)
    trims_applied: int = 0
    trims_rejected: int = 0
    ends_refined: int = 0
    ends_refine_rejected: int = 0


def _moment(show: Show, beat: Beat) -> Moment:
    return Moment(
        beat_id=beat.id,
        episode=beat.episode_number,
        ts=format_timecode(beat.start_s),
        kind=beat.type.value,
        speaker=beat.speaker,
        text=beat.text,
    )


def _segments(segs: list[Segment]) -> list[PromoSegment]:
    return [
        PromoSegment(
            episode=s.episode_number, start_s=s.start_s, end_s=s.end_s,
            start_ts=format_timecode(s.start_s), end_ts=format_timecode(s.end_s),
        )
        for s in segs
    ]  # fmt: skip


def _try_trim(show: Show, f: Fused, beats: dict[str, Beat]) -> list[Segment] | None:
    llm, c = f.llm, f.cand
    if llm is None or c.kind != "single" or not (llm.trim_start_beat_id or llm.trim_end_beat_id):
        return None
    seg = c.segments[0]
    s2, e2, sid, eid = seg.start_s, seg.end_s, seg.start_beat_id, seg.end_beat_id
    if llm.trim_start_beat_id and (b := beats.get(llm.trim_start_beat_id)):
        s2, sid = b.start_s, b.id
    if llm.trim_end_beat_id and (b := beats.get(llm.trim_end_beat_id)):
        e2, eid = b.end_s, b.id
    if e2 <= s2 or (s2, e2) == (seg.start_s, seg.end_s):
        return None
    new = Segment(episode_number=seg.episode_number, start_s=s2, end_s=e2,
                  start_beat_id=sid, end_beat_id=eid)  # fmt: skip
    if validate_segments(show, [new]):
        return None
    hook = beats.get(llm.hook_beat_id or "")
    cliff = beats.get(llm.cliff_beat_id or "")
    if hook is None or not (s2 - 1e-6 <= hook.start_s <= s2 + HOOK_WINDOW_S + 1e-6):
        return None
    if cliff is None or not (e2 - CLIFF_WINDOW_S <= cliff.end_s <= e2 + 1e-6):
        return None
    return [new]


def _ends_at_episode_end(show: Show, segs: list[Segment]) -> bool:
    last = segs[-1]
    ep = show.episode(last.episode_number)
    end = max((b.end_s for b in ep.beats), default=0.0) if ep else 0.0
    return last.end_s >= end - 0.25


def _rule_why(f: Fused) -> str:
    s = f.cand.signals
    bits = []
    if s.hook_terms:
        bits.append(f"opening has hook cues ({', '.join(s.hook_terms[:3])})")
    if s.ends_at_episode_end:
        bits.append("cuts at the episode's end")
    elif s.cliff_cues:
        bits.append(f"ending cues: {', '.join(s.cliff_cues[:2])}")
    return "Rule-based pick: " + ("; ".join(bits) if bits else "highest rule score") + "."


def _risks(f: Fused) -> list[str]:
    risks = list(f.cand.signals.risks)
    if f.llm:
        if f.llm.clarity_gap:
            risks.append(f"needs context: {f.llm.clarity_gap}")
        if f.llm.coherence_gap:
            risks.append(f"stitch gap: {f.llm.coherence_gap}")
        if f.llm.spoiler_risk is not None and f.llm.spoiler_risk >= 7:
            risks.append("spoiler_risk_high")
    return risks


def _post_first(f: Fused, rank: int) -> str:
    top = sorted(f.dims, key=lambda k: -f.dims[k])[:2]
    strengths = " and ".join(_PHRASE[k].format(v=f.dims[k]) for k in top)
    low = min(f.dims, key=lambda k: f.dims[k])
    lead = "Post first because it" if rank == 1 else f"#{rank}: it"
    tail = f" Weakest point: {low} ({f.dims[low]:.0f}/10)." if f.dims[low] < 5 else ""
    return f"{lead} {strengths}.{tail}"


def finalize(
    show: Show, ordered: list[Fused], apply_trim: bool = True
) -> tuple[list[Promo], FinalizeStats]:
    beats = {b.id: b for ep in show.episodes for b in ep.beats}
    stats = FinalizeStats()
    promos: list[Promo] = []
    for f in ordered:
        c, llm = f.cand, f.llm
        segs = c.segments
        trimmed = False
        if apply_trim:
            new = _try_trim(show, f, beats)
            if new:
                segs, trimmed = new, True
                stats.trims_applied += 1
            elif llm and (llm.trim_start_beat_id or llm.trim_end_beat_id) and c.kind == "single":
                stats.trims_rejected += 1
        refined = False
        original = c.segments[0] if c.kind == "single" else None
        if (
            not trimmed and llm is not None and llm.end_changed and llm.end_beat_id
            and c.kind == "single" and original is not None
        ):  # fmt: skip
            eb = beats.get(llm.end_beat_id)
            new_seg = (
                Segment(episode_number=original.episode_number, start_s=original.start_s,
                        end_s=eb.end_s, start_beat_id=original.start_beat_id, end_beat_id=eb.id)
                if eb is not None and eb.end_s > original.start_s else None
            )  # fmt: skip
            # start is kept; code re-validates the cut and recomputes the duration (20-60 s etc.)
            if new_seg is not None and not validate_segments(show, [new_seg]):
                segs, refined = [new_seg], True
                stats.ends_refined += 1
            else:
                stats.ends_refine_rejected += 1
        errors = validate_segments(show, segs)
        hook_id = llm.hook_beat_id if llm and llm.main_ok else c.signals.hook_beat_id
        cliff_id = llm.cliff_beat_id if llm and llm.main_ok else c.signals.cliff_beat_id
        if refined and llm is not None:
            cliff_id = llm.end_beat_id  # the delivered cut is the new cliffhanger moment
        hook, cliff = beats.get(hook_id or ""), beats.get(cliff_id or "")
        if hook is None or cliff is None:
            errors.append("missing_hook_or_cliff_beat")
        else:
            first, last = segs[0], segs[-1]
            if not (first.start_s - 1e-6 <= hook.start_s <= first.start_s + HOOK_WINDOW_S + 1e-6
                    and hook.episode_number == first.episode_number):  # fmt: skip
                errors.append("hook_not_in_first_3s")
            if not (cliff.episode_number == last.episode_number
                    and last.start_s - 1e-6 <= cliff.start_s < last.end_s):  # fmt: skip
                errors.append("cliffhanger_not_in_last_segment")
        if errors or hook is None or cliff is None:
            stats.validation_failures.append((c.id, errors))
            continue
        rank = len(promos) + 1
        sig = c.signals
        scores = SubScores(
            hook=round(f.dims["hook"], 2), cliffhanger=round(f.dims["cliffhanger"], 2),
            clarity=round(f.dims["clarity"], 2), intensity=round(f.dims["intensity"], 2),
            spoiler_risk=llm.spoiler_risk if llm and llm.main_ok else None,
            coherence=llm.coherence if llm else None,
            rule_hook=sig.hook, rule_cliffhanger=sig.cliffhanger,
            rule_intensity=sig.intensity, rule_clarity=sig.clarity,
            llm_hook=llm.hook if llm and llm.main_ok else None,
            llm_cliffhanger=llm.ranking_cliffhanger if llm and llm.main_ok else None,
            llm_intensity=llm.intensity if llm and llm.main_ok else None,
            llm_clarity=llm.clarity if llm and llm.clarity_ok else None,
            rerank_rank=round(f.rerank_rank) if f.rerank_rank is not None else None,
        )  # fmt: skip
        promos.append(
            Promo(
                rank=rank, id=c.id, kind=c.kind, segments=_segments(segs),
                total_duration_s=round(sum(s.end_s - s.start_s for s in segs), 2),
                hook=_moment(show, hook), cliffhanger=_moment(show, cliff),
                why=(llm.why if llm and llm.main_ok and llm.why else _rule_why(f)),
                post_first_because=_post_first(f, rank), scores=scores,
                rule_score=c.rule_score, final_score=f.blended, confidence=f.confidence,
                risks=_risks(f), flags=f.flags, trim_applied=trimmed,
                ends_at_episode_end=_ends_at_episode_end(show, segs),
                end_refined=refined,
                original_end_ts=format_timecode(original.end_s) if refined and original else None,
                original_duration_s=round(original.end_s - original.start_s, 2)
                if refined and original else None,
            )
        )  # fmt: skip
    return promos, stats
