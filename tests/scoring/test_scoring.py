from __future__ import annotations

import math

import pytest
from promo_common.cuts import CONTENT_TYPES
from promo_common.llm.cache import MemoryCache
from promo_common.llm.errors import DailyQuotaExhausted
from promo_common.schemas import BeatType
from scoring_service import Scorer, rerank
from scoring_service.ids import IdMap, build_view
from scoring_service.text import make_redactor

from tests.shared import SpyMock, make_llm


def test_views_offer_only_eligible_non_camera_beats(show, cands) -> None:
    ids = IdMap.build(show)
    for c in cands:
        v = build_view(c, show, ids)
        first = v.segs[0]
        assert v.hook_ok and v.cliff_ok
        for sid in v.hook_ok:
            b = ids.to_beat[sid]
            assert b.type in CONTENT_TYPES and first.start <= b.start_s <= first.start + 3.0 + 1e-6
        for sid in v.cliff_ok:
            assert ids.to_beat[sid].type is not BeatType.CAMERA
        assert all(i.startswith("e") and "_b" in i for i in v.all_ids)


def test_blind_clarity_never_sees_logline_or_continuity(show, cands, scfg) -> None:
    spy = SpyMock()
    Scorer(make_llm(spy), show, scfg).score(cands)
    by_task: dict[str, list[str]] = {}
    for r in spy.requests:
        by_task.setdefault(r.task, []).append(r.system + "\n" + r.user)
    assert {"score", "clarity", "coherence"} <= set(by_task)
    assert any("ZZLOGLINE" in t for t in by_task["score"])  # context-aware judge may see it
    for task in ("clarity", "coherence"):
        for text in by_task[task]:
            assert "ZZLOGLINE" not in text and "ZZNOTES" not in text, task
            assert "e1_b" not in text  # no beat ids either: it is a stranger's view
    assert any("stranger" in t.lower() for t in by_task["clarity"])


def test_all_candidates_scored_by_id_and_text_never_returned(show, cands, scfg) -> None:
    out = Scorer(make_llm(), show, scfg).score(cands)
    ids = IdMap.build(show)
    beats = {b.id for e in show.episodes for b in e.beats}
    for c in cands:
        s = out[c.id]
        assert s.main_ok and s.clarity_ok
        assert s.hook_beat_id in beats and s.cliff_beat_id in beats  # full ids mapped by code
        hook = ids.full_to_beat[s.hook_beat_id]
        assert (
            hook.type in CONTENT_TYPES
            and c.segments[0].start_s <= hook.start_s <= c.segments[0].start_s + 3.0 + 1e-6
        )
        assert (s.coherence_ok is None) == (c.kind == "single")
        assert 0 <= s.hook <= 10 and len(s.why.split()) <= 25


def test_unknown_id_is_rejected_and_only_that_batch_is_retried(show, cands, scfg) -> None:
    spy = SpyMock(faults=["unknown_id"])
    out = Scorer(make_llm(spy), show, scfg).score(cands)
    assert all(s.main_ok for s in out.values())  # recovered via retry
    scores = [r for r in spy.requests if r.task == "score"]
    retry = [r for r in scores if "REJECTED" in r.user]
    assert len(retry) == 1 and "not hook-eligible" in retry[0].user
    first_batch = {c["label"] for c in scores[0].meta["candidates"]}
    assert {c["label"] for c in retry[0].meta["candidates"]} == first_batch  # only the failed ones


def test_hook_after_three_seconds_is_rejected(show, cands, scfg) -> None:
    spy = SpyMock(faults=["late_hook"])
    out = Scorer(make_llm(spy), show, scfg).score(cands)
    assert all(s.main_ok for s in out.values())
    assert any("REJECTED" in r.user for r in spy.requests if r.task == "score")


def test_invalid_json_is_retried(show, cands, scfg) -> None:
    spy = SpyMock(faults=["invalid_json"])
    out = Scorer(make_llm(spy), show, scfg).score(cands)
    assert all(s.main_ok for s in out.values())
    assert any("not valid JSON" in r.user for r in spy.requests if r.task == "score")


def test_persistent_garbage_falls_back_without_crashing(show, cands, scfg) -> None:
    spy = SpyMock(faults=["unknown_id"] * 3)  # first batch fails all 3 attempts
    scorer = Scorer(make_llm(spy), show, scfg)
    out = scorer.score(cands)
    bad = [s for s in out.values() if not s.main_ok]
    assert bad and all(any("score:" in e for e in s.errors) for s in bad)
    assert len(bad) < len(out)  # the other batches are fine
    assert scorer.stats["score_failed"] == len(bad)


def test_out_of_candidate_trim_id_is_rejected(show, cands, scfg) -> None:
    class BadTrim(SpyMock):
        def _score(self, meta, noise, fault):
            out = super()._score(meta, noise, fault)
            out["results"][0]["te"] = "e99_b999"
            return out

    spy = BadTrim()
    out = Scorer(make_llm(spy), show, scfg).score(cands)
    assert any(not s.main_ok for s in out.values())  # the bad trim id repeats on every attempt
    assert any("not a beat of this candidate" in r.user for r in spy.requests if r.task == "score")


def test_why_is_capped_to_25_words(show, cands, scfg) -> None:
    class Wordy(SpyMock):
        def _score(self, meta, noise, fault):
            out = super()._score(meta, noise, fault)
            for r in out["results"]:
                r["why"] = " ".join(["word"] * 60)
            return out

    out = Scorer(make_llm(Wordy()), show, scfg).score(cands)
    assert all(len(s.why.split()) == 25 for s in out.values() if s.main_ok)


def test_second_run_is_free_and_identical(show, cands, scfg) -> None:
    cache = MemoryCache()
    llm = make_llm(SpyMock(), cache)
    a = Scorer(llm, show, scfg).score(cands)
    billed = len(llm.records)
    llm2 = make_llm(SpyMock(), cache)
    b = Scorer(llm2, show, scfg).score(cands)
    assert len(llm2.records) == 0 and billed > 0
    assert {k: v.model_dump() for k, v in a.items()} == {k: v.model_dump() for k, v in b.items()}


def test_quota_stop_then_resume_does_not_rebill_scored_batches(show, cands, scfg) -> None:
    baseline = make_llm(SpyMock())
    Scorer(baseline, show, scfg).score(cands)
    total = len(baseline.records)

    cache = MemoryCache()
    first = make_llm(SpyMock(faults=["ok"] * 4 + ["daily_quota"]), cache)
    with pytest.raises(DailyQuotaExhausted):
        Scorer(first, show, scfg).score(cands)
    billed_first = len(first.records)
    assert billed_first == 4

    second = make_llm(SpyMock(), cache)
    out = Scorer(second, show, scfg).score(cands)
    assert all(s.main_ok and s.clarity_ok for s in out.values())
    assert billed_first + len(second.records) == total  # nothing was paid for twice


def test_offline_mode_never_calls_the_llm(show, cands, scfg) -> None:
    spy = SpyMock()
    sc = Scorer(make_llm(spy), show, scfg)
    sc.offline = True
    out = sc.score(cands)
    assert spy.requests == [] and not any(s.main_ok for s in out.values())
    assert all("offline" in " ".join(s.errors) for s in out.values())


def test_redactor_is_identity_when_nothing_matches_so_cache_is_reused(show, cands, scfg) -> None:
    red = make_redactor(["cliffhanger"])
    assert red("  odd   spacing stays ") == "  odd   spacing stays "
    assert red("an abrupt cliffhanger cut") == "an abrupt cut"
    cache = MemoryCache()
    Scorer(make_llm(SpyMock(), cache), show, scfg).score(cands)
    llm2 = make_llm(SpyMock(), cache)
    Scorer(llm2, show, scfg, redact_terms=["cliffhanger"]).score(cands)
    assert len(llm2.records) == 0  # no script line contains the term: everything is a cache hit


def test_ablation_redaction_recomputes_only_affected_candidates(show, cands, scfg) -> None:
    cache = MemoryCache()
    Scorer(make_llm(SpyMock(), cache), show, scfg).score(cands)
    # plant the literal annotation in one beat's text, then compare redacted vs plain scoring
    victim = show.episodes[0].beats[10]
    victim.text += " abrupt cliffhanger cut"
    hit = [
        c
        for c in cands
        if any(s.episode_number == 1 and s.start_s <= victim.start_s < s.end_s for s in c.segments)
    ]
    assert hit
    llm2 = make_llm(SpyMock(), cache)
    sc = Scorer(llm2, show, scfg, redact_terms=["cliffhanger"])
    sc.score(cands)
    # only candidates containing the victim beat change text, so only they are re-scored
    assert sc.stats["score_cached"] == len(cands) - len(hit)


def test_blind_text_and_digest_respect_redaction(show, cands, scfg) -> None:
    show.episodes[0].beats[10].text += " abrupt cliffhanger cut"
    sc = Scorer(make_llm(), show, scfg, redact_terms=["cliffhanger"])
    out = sc.score(cands)
    for c in cands:
        assert "cliffhanger" not in sc.digest(c, out[c.id]).lower()


# ---- rerank --------------------------------------------------------------------------------
def _digests(n: int) -> tuple[dict[str, str], dict[str, float]]:
    return {f"c{i}": f"digest {i}" for i in range(n)}, {f"c{i}": 10.0 - i for i in range(n)}


def test_rerank_returns_ranks_for_every_candidate(scfg) -> None:
    d, prior = _digests(12)
    ranks = rerank(make_llm(), scfg, d, prior)
    assert ranks is not None and set(ranks) == set(d)
    assert sorted(ranks.values())[0] >= 1 and max(ranks.values()) <= 12
    assert min(ranks, key=lambda k: ranks[k]) == "c0"  # mock follows the prior


def test_rerank_uses_two_passes_with_reversed_order(scfg) -> None:
    d, prior = _digests(6)
    spy = SpyMock()
    rerank(make_llm(spy), scfg, d, prior)
    calls = [r for r in spy.requests if r.task == "rerank"]
    assert len(calls) == scfg.rerank_passes == 2
    first = [ln.split(":")[0] for ln in calls[0].user.splitlines() if ln[:1] == "r"]
    second = [ln.split(":")[0] for ln in calls[1].user.splitlines() if ln[:1] == "r"]
    assert first and len(first) == len(second) == 6


def test_rerank_rejects_bad_permutation_and_retries(scfg) -> None:
    d, prior = _digests(8)
    spy = SpyMock(faults=["bad_permutation"])
    ranks = rerank(make_llm(spy), scfg, d, prior)
    assert ranks is not None
    assert any("EVERY id" in r.user for r in spy.requests if r.task == "rerank")


def test_rerank_gives_up_cleanly(scfg) -> None:
    d, prior = _digests(5)
    spy = SpyMock(faults=["invalid_json"] * 20)
    assert rerank(make_llm(spy), scfg, d, prior) is None


def test_rerank_is_cached(scfg) -> None:
    d, prior = _digests(6)
    cache = MemoryCache()
    rerank(make_llm(SpyMock(), cache), scfg, d, prior)
    llm2 = make_llm(SpyMock(), cache)
    rerank(llm2, scfg, d, prior)
    assert len(llm2.records) == 0


def test_batch_counts_match_design(show, cands, scfg) -> None:
    spy = SpyMock()
    Scorer(make_llm(spy), show, scfg).score(cands)
    n_st = sum(c.kind == "stitched" for c in cands)
    calls = [r.task for r in spy.requests]
    assert calls.count("clarity") == math.ceil(len(cands) / scfg.clarity_batch)
    assert calls.count("coherence") == math.ceil(n_st / scfg.stitched_batch)


# ---- regressions from the first real Gemini smoke run -----------------------------------------
def test_uppercase_ids_from_the_model_are_accepted(show, cands, scfg) -> None:
    """Gemini answered "C1" (copying the prompt header); we expected "c1" and rejected everything."""
    spy = SpyMock(upper_ids=True)
    out = Scorer(make_llm(spy), show, scfg).score(cands)
    assert all(s.main_ok and s.clarity_ok for s in out.values())
    assert not any("REJECTED" in r.user for r in spy.requests)  # no wasted retries


def test_prompt_labels_match_the_label_the_model_must_return(show, cands, scfg) -> None:
    spy = SpyMock()
    Scorer(make_llm(spy), show, scfg).score(cands)
    req = next(r for r in spy.requests if r.task == "score")
    assert "\nc1 (" in req.user and "\nC1 (" not in req.user
    assert "own eligible lists" in req.system.lower() and "double quotes" in req.system
