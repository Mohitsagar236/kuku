"""quality_report.md: metrics, calibration, ablation, stability, golden set and failure modes."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from promo_finder.domain.models import Candidate, LLMScores, Promo, Show
from promo_finder.evaluation.feedback import FeedbackSummary, render_section
from promo_finder.evaluation.golden import GoldenReport
from promo_finder.evaluation.matching import promo_iou, topk_overlap
from promo_finder.evaluation.stability import StabilityReport
from promo_finder.ranking import RankResult

TOP_K = 10


def eff_k(*lists: Any) -> int:
    """Effective top-k: 10, or fewer when a run delivered fewer promos."""
    return min([TOP_K, *(len(x) for x in lists)])


def dependence_verdict(overlap: int, k: int) -> str:
    """Verdict from the overlap *fraction*, so 3/3 identical is ROBUST, not STRONG."""
    if k <= 0:
        return "n/a: no promos to compare"
    f = overlap / k
    if f >= 0.8:
        return "ROBUST: the ranking barely depends on the annotation"
    if f >= 0.5:
        return "MODERATE dependence on how this writer annotates scripts"
    return (
        "STRONG dependence: ranking changes a lot without the annotation, so a "
        "show written differently could break the tool"
    )


def signal_occurrences(show: Show, terms: list[str]) -> int:
    """How many beats mention any of the ablated literal cues (case-insensitive)."""
    low = [t.lower() for t in terms if t]
    return sum(1 for ep in show.episodes for b in ep.beats if any(t in b.text.lower() for t in low))


@dataclass
class AblationInfo:
    name: str
    label: str
    result: RankResult
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReportInput:
    show: Show
    cands: list[Candidate]
    scores: dict[str, LLMScores]
    result: RankResult
    scorer_stats: dict[str, int]
    llm_summary: dict[str, Any]
    gen_summary: dict[str, Any]
    ablations: list[AblationInfo] = field(default_factory=list)
    baseline: RankResult | None = None
    cascade: dict[str, Any] = field(default_factory=dict)
    rcfg: Any = None  # RankingConfig (formula + gates), optional
    stability: StabilityReport | None = None
    golden: GoldenReport | None = None
    feedback: FeedbackSummary | None = None
    feedback_error: str = ""
    partial: bool = False
    est_out_tokens_per_candidate: int = 120


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(a) != len(b):
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else None


def _dist(vals: list[float]) -> str:
    if not vals:
        return "n/a"
    q = (
        statistics.quantiles(vals, n=4)
        if len(vals) >= 4
        else [min(vals), statistics.median(vals), max(vals)]
    )
    return f"{min(vals):.1f} / {q[0]:.1f} / {statistics.median(vals):.1f} / {q[-1]:.1f} / {max(vals):.1f}"


def _ref(p: Promo) -> str:
    segs = " + ".join(f"ep{s.episode} {s.start_ts}-{s.end_ts}" for s in p.segments)
    return f"#{p.rank} ({segs})"


def _examples(items: list[Promo], n: int = 3) -> list[str]:
    return [f'  - {_ref(p)}, hook: "{p.hook.text[:70]}"' for p in items[:n]]


def _segs(c: Candidate) -> list[Any]:
    return [
        SimpleNamespace(episode=g.episode_number, start_s=g.start_s, end_s=g.end_s)
        for g in c.segments
    ]


def _best_candidate(r: ReportInput, segs: list[Any]) -> tuple[Candidate | None, float]:
    best, bi = None, 0.0
    for c in r.cands:
        v = promo_iou(segs, _segs(c))
        if v > bi:
            best, bi = c, v
    return (best, bi) if bi >= 0.5 else (None, bi)


def _why_moved(c: Candidate, s: LLMScores | None, up: bool) -> str:
    """Largest LLM-vs-rule difference among hook / cliffhanger / clarity, in plain words."""
    if s is None or not s.main_ok:
        return "no LLM score for it"
    sig = c.signals
    deltas = {
        "hook": (s.hook or 0) - sig.hook,
        "cliffhanger": (s.cliffhanger or 0) - sig.cliffhanger,
    }
    if s.clarity_ok and s.clarity is not None:
        deltas["blind clarity"] = s.clarity - sig.clarity
    k = max(deltas, key=lambda x: deltas[x] if up else -deltas[x])
    llm_v = {"hook": s.hook, "cliffhanger": s.cliffhanger, "blind clarity": s.clarity}[k]
    rule_v = {"hook": sig.hook, "cliffhanger": sig.cliffhanger, "blind clarity": sig.clarity}[k]
    out = f"LLM {k} {llm_v:.0f} vs rule {rule_v:.1f}"
    if not up and s.clarity_gap:
        out += f"; needs context: {s.clarity_gap}"
    if up and s.why:
        out += f"; {s.why}"
    return out


def _gates_and_refinement(r: ReportInput) -> list[str]:
    res, g = r.result, (r.rcfg.gates if r.rcfg else None)
    L = ["## 2b. Hard gates and end-point refinement", ""]
    if g:
        n = res.n_ranked or len(res.fused)
        first = r.cascade.get("gate_hook_first_pass", 0)
        gt = res.gated
        L += [
            f"- Gates on the LLM's own scores (never delivered below): hook < {g.min_hook:g}, "
            f"cliffhanger < {g.min_cliffhanger:g} (cliffhanger = the end that is delivered), "
            f"blind clarity < {g.min_clarity:g}. "
            "Rule-only fallback candidates are not gated.",
            f"- Removed before blind scoring (hook gate, first pass): {first} of {len(r.cands)} candidates",
            f"- Removed after blind scoring + end refinement, of {n} fully scored: "
            f"hook only {gt.get('hook', 0)}, cliffhanger only {gt.get('cliffhanger', 0)}, "
            f"clarity only {gt.get('clarity', 0)}, several {gt.get('both', 0)} (total {sum(gt.values())})",
        ]
    fs = res.final_stats
    refined = [p for p in res.promos if p.end_refined]
    L += [
        f"- Start snap: {fs.starts_snapped} segment start(s) moved back (<= 0.6 s) onto the camera "
        "shot that introduces the first line, so clips do not open mid-shot",
        f"- End refinement: {fs.ends_refined} delivered promo(s) had their end moved to a stronger "
        f"unresolved cut; {fs.ends_refine_rejected} proposals were rejected by validation",
    ]
    for p in refined[:5]:
        seg = p.segments[-1]
        L.append(f"  - #{p.rank} ep{seg.episode}: end {p.original_end_ts} -> {seg.end_ts}, "
                 f"duration {p.original_duration_s}s -> {p.total_duration_s}s")  # fmt: skip
    return L + [""]


def _formula_section(r: ReportInput) -> list[str]:
    if r.rcfg is None:
        return []
    from promo_finder.ranking.fusion import attribution

    f, w = r.rcfg.fusion, r.rcfg.fusion.weights
    rule = llm = 0.0
    for fu in r.result.selected:
        a, b = attribution(fu, r.rcfg)
        rule += a
        llm += b
    tot = rule + llm
    # config-level share if every signal were LLM-scored
    cfg_llm = (w.hook + w.cliffhanger + w.intensity) * f.llm_share + w.clarity * f.clarity_llm_share
    rw = r.rcfg.rerank.weight
    L = [
        "## 2c. Final-score formula and rule vs LLM share",
        "",
        "```",
        f"dim   = llm_share * LLM + (1 - llm_share) * rule    (hook/cliffhanger/intensity: {f.llm_share:g}; clarity: {f.clarity_llm_share:g})",
        f"score = {w.hook:g}*hook + {w.cliffhanger:g}*cliffhanger + {w.clarity:g}*clarity + {w.intensity:g}*intensity - {f.spoiler_penalty_per_point:g}*spoiler_risk",
        f"stitched: score *= {f.stitched_base:g} + 0.03*coherence",
        f"shortlist (top {r.rcfg.rerank.pool}): final = {1 - rw:g}*score + {rw:g}*listwise-rerank score (LLM)",
        "```",
        f"- By construction the LLM contributes {100 * cfg_llm:.0f}% of the absolute score (rule {100 * (1 - cfg_llm):.0f}%), "
        f"plus 100% of the spoiler penalty and of the rerank blend.",
        f"- Measured on this run's selected promos (before rerank): rule {100 * rule / tot:.0f}% / LLM {100 * llm / tot:.0f}%"
        if tot
        else "- (no selected promos)",
        f"- After the rerank blend, the rule signals' share of the final score is about {100 * (1 - rw) * (1 - cfg_llm):.0f}%.",
        "",
    ]
    return L


def _baseline_section(r: ReportInput) -> list[str]:
    L = ["## 5b. Rules baseline vs rules + LLM", ""]
    if r.baseline is None:
        return L + ["- No rules baseline available for this run.", ""]
    k = eff_k(r.baseline.promos, r.result.promos)
    base = r.baseline.promos[:k]
    llm10 = r.result.promos[:k]
    ov = topk_overlap([p.segments for p in llm10], [p.segments for p in base])
    L += [f"- Top-{k} overlap, LLM run vs rules-only baseline: **{ov}/{k}** (IoU >= 0.5 = same promo)",
          f"- Top-{k} ending at an episode's end: LLM run {sum(p.ends_at_episode_end for p in llm10)}/{k}, "
          f"rules baseline {sum(p.ends_at_episode_end for p in base)}/{k}", ""]  # fmt: skip
    promoted = [p for p in llm10 if not any(promo_iou(p.segments, b.segments) >= 0.5 for b in base)]
    L.append(f"**Promoted by the LLM** (in its top {k}, not in the rules top {k}):")
    for p in promoted or []:
        c, _ = _best_candidate(r, p.segments)
        rb = next(
            (b.rank for b in r.baseline.promos if promo_iou(p.segments, b.segments) >= 0.5), None
        )
        L.append(f"- LLM #{p.rank} {_ref(p)[len(f'#{p.rank} '):]}; rules rank {rb or '>' + str(len(r.baseline.promos))}: "
                 f"{_why_moved(c, r.scores.get(c.id), True) if c else 'n/a'}")  # fmt: skip
    if not promoted:
        L.append(f"- none: the same {k} promos, possibly reordered")
    dropped = [b for b in base if not any(promo_iou(b.segments, p.segments) >= 0.5 for p in llm10)]
    L += ["", f"**Demoted by the LLM** (in the rules top {k}, not in its top {k}):"]
    by_id = {p.id: p for p in r.result.promos}
    for b in dropped:
        c, _ = _best_candidate(r, b.segments)
        seg = " + ".join(f"ep{g.episode} {g.start_ts}-{g.end_ts}" for g in b.segments)
        if c is None:
            status = "not among the LLM run's candidates"
            why = "candidate set differs"
        else:
            s = r.scores.get(c.id)
            lp = by_id.get(c.id)
            if lp:
                status = f"LLM rank {lp.rank}"
            elif s is not None and not s.stage2:
                status = "cut after first-pass scoring"
            else:
                status = "outside the delivered list (score, dedupe or caps)"
            why = _why_moved(c, s, False)
        L.append(f"- rules #{b.rank} ({seg}): {status}; {why}")
    if not dropped:
        L.append("- none")
    L.append("")
    return L


def build_report(r: ReportInput) -> str:
    L: list[str] = []
    promos = r.result.promos
    top10 = promos[:10]
    st = r.scorer_stats
    L += [f"# Quality report: {r.show.title}", ""]
    L += [
        "## 1. Run summary", "",
        f"- Episodes: {len(r.show.episodes)}; beats parsed: {sum(len(e.beats) for e in r.show.episodes)}",
        f"- Candidate windows examined: {r.gen_summary.get('windows', 0):,}; sent to scoring: {len(r.cands)}"
        f" ({sum(c.kind == 'stitched' for c in r.cands)} stitched)",
        f"- Cascade: hook/cliffhanger/intensity scored on all {len(r.cands)}; blind clarity + stitched "
        f"coherence on the top {r.cascade.get('stage2', len(r.cands))} after first-pass scoring "
        f"(the rest cannot be delivered)",
        f"- Promos delivered: {len(promos)} (top {eff_k(promos)} shown in ranking.md)",
        f"- LLM: {r.llm_summary.get('provider')} / {r.llm_summary.get('model')}, "
        f"{r.llm_summary.get('calls')} calls, {r.llm_summary.get('tokens_in', 0):,} in / "
        f"{r.llm_summary.get('tokens_out', 0):,} out tokens",
        f"- Complete run: {'NO, partial or rules-only (LLM disabled, or quota/budget stop): unscored candidates use RULE scores only' if r.partial else 'yes'}",
        "",
    ]  # fmt: skip

    # 2. reliability
    fails = r.result.final_stats.validation_failures
    total_sel = len(promos) + len(fails)
    L += ["## 2. Validation and reliability", ""]
    L += [
        f"- Final validation failures: {len(fails)} of {total_sel} selected "
        f"({100 * len(fails) / max(1, total_sel):.1f}%)"
        + (": " + ", ".join(f"{cid}:{'/'.join(codes)}" for cid, codes in fails[:5]) if fails else ""),
        f"- LLM answers rejected after retries (candidate-level): score {st.get('score_failed', 0)}, "
        f"clarity {st.get('clarity_failed', 0)}, coherence {st.get('coherence_failed', 0)}; "
        f"served from cache: score {st.get('score_cached', 0)}, clarity {st.get('clarity_cached', 0)}",
        f"- Candidates that fell back to rule scores: "
        f"{sum('rule_fallback_main' in f.flags for f in r.result.fused)} (hook/cliff/intensity), "
        f"{sum('rule_fallback_clarity' in f.flags for f in r.result.fused)} (clarity)",
        f"- LLM trim suggestions applied: {r.result.final_stats.trims_applied}, "
        f"rejected (would break a rule): {r.result.final_stats.trims_rejected}",
        f"- Dropped by selection: {dict(r.result.dropped) or 'none'}",
        "",
    ]  # fmt: skip

    L += _gates_and_refinement(r)
    L += _formula_section(r)

    # 3. distributions
    L += ["## 3. Score distributions", "", "min / q1 / median / q3 / max", "",
          "| dimension | delivered promos | all scored candidates |", "|---|---|---|"]  # fmt: skip
    for dim in ("hook", "cliffhanger", "clarity", "intensity"):
        pv = [getattr(p.scores, dim) for p in promos]
        av = [f.dims[dim] for f in r.result.fused]
        L.append(f"| {dim} | {_dist(pv)} | {_dist(av)} |")
    L += [f"| final score | {_dist([p.final_score for p in promos])} | "
          f"{_dist([f.final for f in r.result.fused])} |", ""]  # fmt: skip

    # 4. LLM vs rule
    pairs = [(c, r.scores[c.id]) for c in r.cands if c.id in r.scores and r.scores[c.id].main_ok]
    L += ["## 4. LLM vs rule disagreement", ""]
    if pairs:
        for dim, rule_attr in (
            ("hook", "hook"),
            ("cliffhanger", "cliffhanger"),
            ("intensity", "intensity"),
        ):
            d = [abs(getattr(s, dim) - getattr(c.signals, rule_attr)) for c, s in pairs]
            L.append(f"- {dim}: mean |LLM-rule| = {statistics.fmean(d):.2f}, share differing by >=4 points: "
                     f"{100 * sum(x >= 4 for x in d) / len(d):.0f}%")  # fmt: skip
        rho = spearman([c.rule_score for c, _ in pairs],
                       [((s.hook or 0.0) + (s.cliffhanger or 0.0) + (s.intensity or 0.0)) / 3 for _, s in pairs])  # fmt: skip
        L.append(f"- Spearman correlation, rule score vs mean LLM score: "
                 f"{'n/a' if rho is None else f'{rho:.2f}'}")  # fmt: skip
        big = sorted(
            pairs,
            key=lambda p: (
                -abs((p[1].hook or 0.0) - p[0].signals.hook)
                - abs((p[1].cliffhanger or 0.0) - p[0].signals.cliffhanger)
            ),
        )[:3]
        L.append("- Biggest disagreements (candidate, LLM hook/cliff vs rule hook/cliff):")
        for c, s in big:
            seg = c.segments[0]
            L.append(f"  - {c.id} ep{seg.episode_number} {seg.start_s:.1f}s: LLM {s.hook:.0f}/{s.cliffhanger:.0f}"
                     f" vs rule {c.signals.hook:.1f}/{c.signals.cliffhanger:.1f}")  # fmt: skip
    else:
        L.append("- No LLM scores available.")
    L.append("")

    # 5. calibration
    L += ["## 5. Calibration (cross-show listwise rerank)", ""]
    if r.result.rerank_used:
        post = [p.id for p in top10]
        kk = eff_k(post, r.result.pre_rerank_top)
        pre, post = r.result.pre_rerank_top[:kk], post[:kk]
        same = len(set(pre) & set(post))
        L.append(f"- Rerank applied to the top {len(r.result.selected[:30])}. Top-{kk} before vs after rerank: "
                 f"{same}/{kk} unchanged ({kk - same} replaced).")  # fmt: skip
    else:
        L.append(
            "- Rerank NOT applied (LLM unavailable or every pass failed): scores are per-episode absolute only."
        )
    L.append("")

    # 5b. rules baseline vs LLM
    L += _baseline_section(r)

    # 6. ablations
    L += ["## 6. Ablations: how much does the tool lean on fragile signals?", ""]
    n_main_end = sum(p.ends_at_episode_end for p in top10)
    L += [f"Main run: {n_main_end}/{len(top10)} of the top {len(top10)} end at an episode's end "
          f"({sum(p.ends_at_episode_end for p in promos)}/{len(promos)} of all delivered). "
          "No cap is applied to this share; it is only reported.", ""]  # fmt: skip
    if r.ablations:
        for ab in r.ablations:
            a_promos = ab.result.promos
            k = eff_k(promos, a_promos)
            if k == 0:
                L += [f"### {ab.name}: {ab.label}",
                      f"- No promos to compare ({len(promos)} main vs {len(a_promos)} without): "
                      "nothing to measure.", ""]  # fmt: skip
                continue
            k30 = min(30, len(promos), len(a_promos))
            ov = topk_overlap([p.segments for p in promos[:k]], [p.segments for p in a_promos[:k]])
            ov30 = topk_overlap(
                [p.segments for p in promos[:30]], [p.segments for p in a_promos[:30]]
            )
            m_end = sum(p.ends_at_episode_end for p in promos[:k])
            a_end10 = sum(p.ends_at_episode_end for p in a_promos[:k])
            a_endall = sum(p.ends_at_episode_end for p in a_promos)
            L += [f"### {ab.name}: {ab.label}",
                  f"- Top-{k} overlap with/without: **{ov}/{k}** (IoU >= 0.5 = same promo); top-{k30}: {ov30}/{k30}",
                  f"- Top-{k} ending at an episode end: **{m_end}/{k} with vs {a_end10}/{k} without**; "
                  f"all delivered: {sum(p.ends_at_episode_end for p in promos)}/{len(promos)} vs {a_endall}/{len(a_promos)}"]  # fmt: skip
            if ab.name == "literal_cliffhanger":
                terms = list(ab.info.get("terms") or [])
                seen = signal_occurrences(r.show, terms)
                if seen == 0:
                    verdict = "not applicable: signal absent in this show (0 annotations to remove)"
                else:
                    verdict = f"{dependence_verdict(ov, k)} ({seen} annotated beats)"
                L.append(f"- Verdict: {verdict}.")
            else:
                L.append(
                    "- Reading: if the share drops a lot without the prior, the prior is steering "
                    "picks; if overlap stays high, the text signals agree with it."
                )
            L.append("")
    else:
        L += ["- Ablations not run.", ""]

    # 7. stability
    L += ["## 7. Stability (temperature 0, re-scored a second time)", ""]
    stability = r.stability
    if stability and stability.per_dim:
        L.append(f"- Model: {r.llm_summary.get('model')}; sampled {stability.n_sampled} candidates "
                 f"(random 10% + the top 10), seed {stability.seed}.")  # fmt: skip
        L += [
            "",
            "| dimension | n | mean abs diff | std of diff | identical | max diff |",
            "|---|---|---|---|---|---|",
        ]
        for dim, stat in stability.per_dim.items():
            L.append(
                f"| {dim} | {stat.n} | {stat.mean_abs_diff} | {stat.std_diff} | {stat.pct_identical}% | {stat.max_abs_diff} |"
            )
        L.append("")
        if stability.top10_changed is not None:
            L.append(f"- Top-10 picks that change when the top 10 are re-scored: **{stability.top10_changed}/{eff_k(r.result.pre_rerank_top)}** "
                     f"(pre-rerank ranking).")  # fmt: skip
        if stability.note:
            L.append(f"- Note: {stability.note}")
    else:
        L.append("- Stability not run.")
    L.append("")

    # 8. golden
    L += ["## 8. Golden set", ""]
    g = r.golden
    if g:
        kg = min(g.k, len(promos))  # a 6-promo run is precision@6, not @10
        L += [f"- {g.n_golden} labelled promos; precision@{kg} = **{g.precision_at_k}**, recall@{kg} = {g.recall_at_k} "
              f"(match = IoU >= {g.iou_threshold})", "",
              "| golden promo | best IoU | rank of best match | in top-k |", "|---|---|---|---|"]  # fmt: skip
        for m in g.matches:
            L.append(
                f"| {m.note[:50] or '(no note)'} | {m.best_iou} | {m.best_rank or '-'} | {'yes' if m.found_at_k else 'no'} |"
            )
    else:
        L.append(
            "- No golden.yaml provided. Copy golden.template.yaml to golden.yaml, label ~10 promos, "
            "re-run. (Or label in the viewer: see 8b.)"
        )
    L.append("")
    if r.feedback_error:
        L += ["## 8b. Editor feedback", "",
              f"- The editor feedback file could not be read: {r.feedback_error}. Fix or delete it.", ""]  # fmt: skip
    else:
        L += [*render_section(r.feedback).splitlines(), ""]

    # 9. failure modes
    L += ["## 9. Where the tool picks badly", "",
          "Detected automatically on the delivered promos; examples are real rows from this run.", ""]  # fmt: skip
    n = max(1, len(promos))
    # the delivered cut (after end refinement), not the candidate's original end
    end_bias = [p for p in promos if p.ends_at_episode_end]
    ctx_dep = [
        p
        for p in promos
        if p.scores.clarity < 5 or any("continuity" in x or "needs context" in x for x in p.risks)
    ]
    spoil = [p for p in promos if (p.scores.spoiler_risk or 0) >= 7]
    stitched = [p for p in promos if p.kind == "stitched"]
    low_coh = [p for p in stitched if (p.scores.coherence or 0) < 5]
    dis = [
        p
        for p in promos
        if p.scores.llm_hook is not None and abs(p.scores.llm_hook - p.scores.rule_hook) >= 4
    ]
    deadair = [p for p in promos if "dead_air" in p.risks]
    eps = sorted({s.episode for p in top10 for s in p.segments})
    modes = [
        ("Episode-ending bias", end_bias, f"{100 * len(end_bias) / n:.0f}% of delivered promos end exactly at an episode's end. "
         "Real cliffhangers, but every episode has one, so this signal alone cannot rank them."),
        ("Needs backstory (low standalone clarity)", ctx_dep, "A stranger would miss who the characters are or what came before."),
        ("Spoils a major beat", spoil, "Reveals an outcome that a promo should tease, not show."),
        ("Stitched clip may confuse newcomers", low_coh or stitched, f"{len(stitched)} stitched promos delivered; flagged lower confidence."),
        ("LLM and rules disagree about the hook", dis, "One of the two is wrong; check these first."),
        ("Dead air inside the clip", deadair, "Long stretch with no dialogue or action."),
    ]  # fmt: skip
    for title, items, why in modes:
        L.append(f"**{title}**: {len(items)} of {len(promos)}. {why}")
        L += _examples(items)
        L.append("")
    L += [
        f"**Coverage**: the top {len(top10)} come from {len(eps)} distinct episodes ({', '.join(map(str, eps))}); "
        f"promos overall from {len({s.episode for p in promos for s in p.segments})} of {len(r.show.episodes)} episodes.",
        "",
        "**Structural limits (not measurable from the script alone)**",
        "- Script-only: no picture or audio, so slaps and music stings are inferred from text.",
        "- Timestamps are the script's; the produced video may differ slightly, so editors should verify cuts.",
        "- LLM judges favour melodrama; a quiet but effective hook scores low.",
        "- Hook/cliffhanger text is judged by one model; use the golden set to check it against your taste.",
        "",
    ]  # fmt: skip

    # 10. cost
    L += ["## 10. Tokens and cost", ""]
    bt = r.llm_summary.get("by_task", {})
    live = st.get("score_live", 0)
    sc = bt.get("score", {})
    if sc and live:
        per = sc["tokens_out"] / live
        L.append(f"- Scoring output tokens per candidate (measured, incl. retries): **{per:.0f}** "
                 f"vs the {r.est_out_tokens_per_candidate} assumed in planning.")  # fmt: skip
    elif st.get("score_cached", 0):
        L.append("- Scoring output tokens per candidate: not measurable in this run (every main-ranking "
                 "answer came from the response cache); the calls below are from phases scored fresh.")  # fmt: skip
    for task, v in bt.items():
        L.append(
            f"- {task}: {int(v['calls'])} calls, {int(v['tokens_in']):,} in / {int(v['tokens_out']):,} out"
        )
    L.append(f"- Estimated cost: ${r.llm_summary.get('cost_usd', 0):.4f} "
             f"({'prices from config/pricing.yaml' if r.llm_summary.get('price_known') else 'model price unknown: shown as 0, check your provider'})")  # fmt: skip
    L.append("")
    return "\n".join(L)
