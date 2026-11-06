"""`make smoke`: a few episodes through the REAL LLM, to find adapter/format bugs for a few calls.

Prints counts only (calls, tokens, JSON problems, truncation), never script text.
"""

from __future__ import annotations

from promo_finder_cli.pipeline import RunResult

EST_OUT_TOKENS = 120  # planning estimate per scored candidate


def smoke_summary(run: RunResult) -> dict:
    """Machine-readable smoke result (kept next to the outputs; used by `compare`)."""
    u = run.llm.summary()
    st = run.main.scorer.stats
    live = max(1, st.get("score_live", 0))
    score_out = sum(r.tokens_out for r in run.llm.records if r.task == "score")
    top = []
    for p in run.main.result.promos[:3]:
        top.append(p.model_dump(mode="json"))
    return {
        "model": f"{run.llm.provider}/{run.llm.model}",
        "episodes": len(run.show.episodes),
        "candidates": len(run.main.cands),
        "fully_scored": run.main.info["ranked_candidates"],
        "promos": len(run.main.result.promos),
        "calls": u["calls"],
        "calls_by_task": {k: int(v["calls"]) for k, v in u["by_task"].items()},
        "tokens_in": u["tokens_in"],
        "tokens_out": u["tokens_out"],
        "score_out_tokens_per_candidate": round(score_out / live, 1),
        "invalid_json": sum(v for k, v in st.items() if k.endswith("_invalid_json")),
        "rejected": sum(v for k, v in st.items() if k.endswith("_rejected")),
        "retry_calls": sum(v for k, v in st.items() if k.endswith("_retry_calls")),
        "still_failing": sum(v for k, v in st.items() if k.endswith("_failed")),
        "truncated": u["truncated_calls"],
        "latency_ms": u["latency_ms"],
        "transient_retries": u["retries"]["by_kind"],
        "gates": {
            "first_pass_hook": run.main.info.get("gate_hook_first_pass", 0),
            "after": dict(run.main.result.gated),
        },  # fmt: skip
        "ends_refined": run.main.result.final_stats.ends_refined,
        "ends_refine_rejected": run.main.result.final_stats.ends_refine_rejected,
        "top3": top,
    }


def smoke_report(run: RunResult) -> tuple[str, bool]:
    u = run.llm.summary()
    st = run.main.scorer.stats
    by = u["by_task"]
    out_tok_main = sum(r.tokens_out for r in run.llm.records if r.task == "score")
    live = max(1, st.get("score_live", 0))
    per_cand = out_tok_main / live
    tasks = ", ".join(f"{k} {int(v['calls'])}" for k, v in by.items()) or "none"
    invalid = sum(v for k, v in st.items() if k.endswith("_invalid_json"))
    rejected = sum(v for k, v in st.items() if k.endswith("_rejected"))
    retry_calls = sum(v for k, v in st.items() if k.endswith("_retry_calls"))
    failed = sum(v for k, v in st.items() if k.endswith("_failed"))
    fallbacks = sum("rule_fallback_main" in f.flags for f in run.main.result.fused)
    rpd = run.llm_settings.rpd

    problems: list[str] = []
    if u["truncated_calls"]:
        problems.append(
            f"{u['truncated_calls']} response(s) hit the output-token limit (truncated JSON). "
            "Check llm_raw/, set LLM_THINKING_BUDGET=0 or raise max_tokens."
        )
    if failed:
        problems.append(
            f"{failed} candidate answer(s) still invalid after retries (rule fallback used)."
        )
    if u["calls"] and invalid / u["calls"] > 0.2:
        problems.append("more than 20% of calls returned unparseable JSON.")
    if by.get("score") and per_cand > 3 * EST_OUT_TOKENS:
        problems.append(
            f"output tokens/candidate {per_cand:.0f} is >3x the {EST_OUT_TOKENS} estimate."
        )
    if by.get("score") and not any(s.main_ok for s in run.main.scores.values()):
        problems.append("no candidate was scored by the LLM at all.")
    if run.main.partial:
        problems.append("run stopped early (quota/budget); see message above.")

    L = [
        "",
        "=== SMOKE RESULT ===",
        f"model: {run.llm.provider}/{run.llm.model}; episodes: {len(run.show.episodes)}; temperature {run.llm.temperature}",
        f"candidates: {len(run.main.cands)} first-pass scored, {run.main.info['ranked_candidates']} fully scored; "
        f"promos delivered: {len(run.main.result.promos)}",
        f"LLM calls made: {u['calls']} ({tasks}); cache hits: {u['cache_hits'] or 'none'}",
        f"tokens: {u['tokens_in']:,} in / {u['tokens_out']:,} out; "
        f"scoring output tokens per candidate: {per_cand:.0f} (planning estimate {EST_OUT_TOKENS})",
        f"JSON problems: unparseable replies {invalid}, candidate answers rejected by validation {rejected}, "
        f"extra retry calls {retry_calls}, still failing after retries {failed}",
        f"truncated responses: {u['truncated_calls']}",
        f"transient retries (error kind): {u['retries']['by_kind'] or 'none'}",
        f"latency per call: mean {u['latency_ms']['mean']:.0f} ms, p95 {u['latency_ms']['p95']:.0f} ms, max {u['latency_ms']['max']:.0f} ms",
        f"gates: hook-gated before stage 2: {run.main.info.get('gate_hook_first_pass', 0)}; "
        f"after: {dict(run.main.result.gated) or 'none'}; "
        f"end refinement: {run.main.result.final_stats.ends_refined} moved, "
        f"{run.main.result.final_stats.ends_refine_rejected} rejected",
        f"candidates on rule-score fallback: {fallbacks}",
        f"daily quota used (client count): {run.llm.limiter.used_today}"
        + (f" of {rpd}" if rpd else ""),
        f"raw responses for inspection: {run.out_dir / 'llm_raw'}",
        "VERDICT: " + ("OK, safe to run the full job." if not problems else "PROBLEMS FOUND"),
    ]
    L += [f"  - {p}" for p in problems]
    return "\n".join(L), not problems
