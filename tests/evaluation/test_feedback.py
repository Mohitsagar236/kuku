"""Editor feedback: verdict file format, golden compatibility, nudges, precision, report section."""

from __future__ import annotations

from pathlib import Path

import pytest

from promo_finder.evaluation import evaluate_golden, load_golden
from promo_finder.evaluation.feedback import (
    FeedbackError,
    FSeg,
    apply_nudge,
    evaluate_feedback,
    load_feedback,
    nudge,
    promo_views,
    render_section,
    replace_section,
    reset_cut,
    save_feedback,
    set_review,
)


def promo(rank: int, ep: int, s: float, e: float) -> dict:
    return {"rank": rank, "id": f"p{rank}", "final_score": 9 - rank,
            "segments": [{"episode": ep, "start_s": s, "end_s": e}]}  # fmt: skip


PROMOS = [promo(1, 1, 0.0, 26.5), promo(2, 1, 40.0, 70.5), promo(3, 2, 0.0, 26.5),
          promo(4, 2, 40.0, 70.5), promo(5, 3, 0.0, 26.5)]  # fmt: skip
V = promo_views(PROMOS)


def reviewed(path: Path) -> list:
    es: list = []
    es = set_review(es, V[0], "r1", "would_post", "perfect opening")
    es = set_review(es, V[1], "r1", "approve", None)
    es = set_review(es, V[2], "r1", "reject", "  spoils\nthe twist ")
    es = set_review(es, V[3], "r1", None, "undecided, maybe")  # a note with no verdict yet
    save_feedback(path, "demo", es)
    return es


def test_file_round_trips_and_keeps_rejected_out_of_the_golden_list(tmp_path: Path) -> None:
    p = tmp_path / "editor_feedback.yaml"
    reviewed(p)
    back = {e.promo_id: e for e in load_feedback(p)}
    assert back["p1"].verdict == "would_post" and back["p1"].note == "perfect opening"
    assert back["p3"].verdict == "reject" and back["p3"].note == "spoils\nthe twist"
    assert back["p4"].verdict is None and back["p4"].note == "undecided, maybe"
    golden = load_golden(p)  # the EXISTING golden loader reads the feedback file as is
    assert [(g.segments[0].episode, g.segments[0].start_s) for g in golden] == [(1, 0.0), (1, 40.0)]
    assert [g.note for g in golden] == ["perfect opening", ""]


def test_precision_matches_the_existing_golden_scorer(tmp_path: Path) -> None:
    p = tmp_path / "f.yaml"
    es = reviewed(p)

    class P:  # the golden scorer wants Promo-like objects
        def __init__(self, d: dict) -> None:
            self.rank = d["rank"]
            self.segments = [FSeg(**{"episode": s["episode"], "start_s": s["start_s"],
                                     "end_s": s["end_s"]}) for s in d["segments"]]  # fmt: skip

    gold = evaluate_golden(load_golden(p), [P(d) for d in PROMOS], k=5, thr=0.5)
    mine = evaluate_feedback(es, PROMOS, k=5)
    assert mine.precision_at_k == gold.precision_at_k == 0.4  # 2 of 5; unreviewed = misses


def test_summary_counts_unreviewed_as_misses_and_reports_reviewed_precision(tmp_path: Path) -> None:
    es = reviewed(tmp_path / "f.yaml")
    s = evaluate_feedback(es, PROMOS, k=4)
    assert (s.top, s.top_positive, s.top_rejected, s.top_unreviewed) == (4, 2, 1, 1)
    assert s.precision_at_k == 0.5 and s.precision_reviewed == pytest.approx(0.667, abs=1e-3)
    assert (s.n_approved, s.n_would_post, s.n_rejected) == (1, 1, 1)
    assert [(r.rank, r.note) for r in s.rejected] == [(3, "spoils\nthe twist")]


def test_nothing_reviewed_means_zero_not_a_crash() -> None:
    s = evaluate_feedback([], PROMOS, k=10)
    assert s.precision_at_k == 0.0 and s.precision_reviewed is None and s.top_unreviewed == 5
    assert evaluate_feedback([], [], k=10).precision_at_k == 0.0


def test_entry_is_found_by_cut_overlap_after_ids_change(tmp_path: Path) -> None:
    es = reviewed(tmp_path / "f.yaml")
    rerun = promo_views([{**d, "id": "NEW" + d["id"]} for d in PROMOS])  # new ids, same cuts
    s = evaluate_feedback(es, [{**d, "id": "NEW" + d["id"]} for d in PROMOS], k=5)
    assert s.top_positive == 2 and s.top_rejected == 1
    assert [r.rank for r in s.rejected] == [3]
    assert rerun[0].id.startswith("NEW")


def test_clearing_a_verdict_removes_an_empty_entry_but_keeps_a_note() -> None:
    es = set_review([], V[0], "r", "approve", None)
    assert set_review(es, V[0], "r", "clear", None) == []
    es = set_review([], V[0], "r", "approve", "why")
    kept = set_review(es, V[0], "r", "clear", None)
    assert len(kept) == 1 and kept[0].verdict is None and kept[0].note == "why"


@pytest.mark.parametrize("bad", ["maybe", "APPROVE", ""])
def test_unknown_verdicts_are_rejected(bad: str) -> None:
    with pytest.raises(FeedbackError):
        set_review([], V[0], "r", bad, None)


def test_overlong_note_is_rejected() -> None:
    with pytest.raises(FeedbackError):
        set_review([], V[0], "r", "approve", "x" * 1001)


# ----------------------------------------------------------------------------- nudges

SEG = [FSeg(episode=1, start_s=0.0, end_s=26.5)]


def test_a_nudge_onto_a_valid_boundary_is_accepted(show) -> None:
    r = nudge(show, SEG, 0, "end", delta=4.0)  # 26.5 -> 30.5 is the end of the next line
    assert r.ok and r.segments[0].end_s == 30.5 and r.reasons == []


def test_half_second_nudge_off_a_beat_is_refused_with_reasons_and_valid_suggestions(show) -> None:
    r = nudge(show, SEG, 0, "end", delta=-0.5)  # 26.0 is inside the gap between lines
    assert not r.ok and r.segments == SEG
    assert any("end of a line" in x for x in r.reasons)
    assert r.suggestions == [22.5, 30.5]  # nearest valid ends either side of the request
    for t in r.suggestions:  # every suggestion really is accepted by the same rules
        assert nudge(show, SEG, 0, "end", to=t).ok


def test_start_nudge_that_breaks_the_duration_rule_is_refused_in_plain_english(show) -> None:
    r = nudge(show, SEG, 0, "start", delta=0.5)
    assert not r.ok and r.suggestions == [4.0]
    short = nudge(show, SEG, 0, "end", delta=-4.0)  # 22.5 is a valid end, but 8.0 start below...
    assert short.ok  # 22.5 s long: still inside 20-60 s
    too_short = nudge(show, short.segments, 0, "end", delta=-4.0)  # 18.5 s
    assert not too_short.ok and any("shorter than 20 s" in x for x in too_short.reasons)


def test_nudges_that_make_no_sense_are_refused(show) -> None:
    assert not nudge(show, SEG, 0, "start", delta=-0.5).ok  # before the episode starts
    assert not nudge(show, SEG, 0, "end", to=-3).ok
    assert not nudge(show, SEG, 0, "start", to=40.0).ok  # start would pass the end
    with pytest.raises(FeedbackError):
        nudge(show, SEG, 0, "middle", delta=0.5)
    with pytest.raises(FeedbackError):
        nudge(show, SEG, 3, "end", delta=0.5)
    with pytest.raises(FeedbackError):
        nudge(show, SEG, 0, "end")
    with pytest.raises(FeedbackError):
        nudge(show, SEG, 0, "end", delta=1, to=2)


def test_nudged_cut_is_what_the_golden_scorer_sees_and_reset_restores_the_original(
    show, tmp_path: Path
) -> None:
    moved = nudge(show, SEG, 0, "end", delta=4.0)
    es = set_review([], V[0], "r", "approve", None)
    es = apply_nudge(es, V[0], "r", moved.segments)
    p = tmp_path / "f.yaml"
    save_feedback(p, "demo", es)
    (g,) = load_golden(p)
    assert g.segments[0].end_s == 30.5  # the editor's final cut
    (back,) = load_feedback(p)
    assert back.nudged and back.original[0].end_s == 26.5
    assert (
        evaluate_feedback([back], PROMOS, k=5).top_positive == 1
    )  # still matches the tool's promo
    es = reset_cut(es, V[0])
    assert not es[0].nudged and es[0].segments[0].end_s == 26.5


def test_a_nudge_alone_keeps_a_pending_entry_that_golden_ignores(show, tmp_path: Path) -> None:
    moved = nudge(show, SEG, 0, "end", delta=4.0)
    es = apply_nudge([], V[0], "r", moved.segments)
    p = tmp_path / "f.yaml"
    save_feedback(p, "demo", es)
    assert load_golden(p) == [] and len(load_feedback(p)) == 1


# ----------------------------------------------------------------------------- file safety


def test_missing_file_is_empty_and_a_malformed_one_is_a_clear_error(tmp_path: Path) -> None:
    assert load_feedback(tmp_path / "nope.yaml") == []
    bad = tmp_path / "bad.yaml"
    bad.write_text("promos: [this is: not: valid")
    with pytest.raises(FeedbackError, match="malformed"):
        load_feedback(bad)
    bad.write_text("promos:\n  - segments: [{episode: 1, start: 'zz', end: '00:10.0'}]\n")
    with pytest.raises(FeedbackError, match="malformed"):
        load_feedback(bad)


# ----------------------------------------------------------------------------- report section


def test_report_section_shows_precision_and_every_rejection_with_its_reason(tmp_path: Path) -> None:
    es = reviewed(tmp_path / "f.yaml")
    text = render_section(evaluate_feedback(es, PROMOS, k=5))
    assert text.startswith("## 8b. Editor feedback")
    assert "precision@5 = 0.40" in text and "2 of the top 5" in text
    assert "lower bound" in text and "2 of the top 5 are still unreviewed" in text
    assert "#3 (ep2 00:00.00-00:26.50): spoils the twist" in text  # whitespace collapsed
    assert "not from the tool or an AI" in text


def test_report_section_with_no_feedback_says_how_to_give_some() -> None:
    assert "No editor feedback yet" in render_section(None)
    assert "No editor feedback yet" in render_section(evaluate_feedback([], PROMOS))


def test_section_is_inserted_before_9_and_replaced_not_duplicated() -> None:
    report = "# R\n## 8. Golden set\n- none\n## 9. Where the tool picks badly\nx\n"
    first = replace_section(report, "## 8b. Editor feedback\n\n- one\n")
    assert first.index("## 8b.") < first.index("## 9.") and "- one" in first
    again = replace_section(first, "## 8b. Editor feedback\n\n- two\n")
    assert again.count("## 8b.") == 1 and "- two" in again and "- one" not in again
    assert "## 9. Where the tool picks badly" in again
    assert replace_section("# only\n", "## 8b. Editor feedback\n\n- x\n").endswith("- x\n")
