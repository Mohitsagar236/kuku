from pathlib import Path

import pytest
from ingestion_service import load_show, parse_episode
from ingestion_service.parser import normalize_speaker
from promo_common.errors import IngestionError
from promo_common.schemas import BeatType

FIX = Path(__file__).parent.parent / "fixtures" / "synthetic_show"


def _ep(n: int):
    return load_show(FIX).episode(n)


def codes(ep) -> list[str]:
    return [w.code for w in ep.warnings]


def test_clean_episode_structure() -> None:
    ep = _ep(1)
    assert ep is not None
    assert ep.number == 1
    assert "lighthouse" in ep.logline
    assert ep.continuity_notes.startswith("First episode")
    assert [s.idx for s in ep.scenes] == [1, 2]
    assert ep.scenes[0].heading == "EXT. HARBOR DOCK – DAY"
    assert (ep.scenes[0].start_s, ep.scenes[0].end_s) == (0.0, 40.0)
    assert ep.duration_s == pytest.approx(80.0)
    assert codes(ep) == []


def test_beat_types_ids_and_order() -> None:
    ep = _ep(1)
    beats = ep.scenes[0].beats
    assert beats[0].id == "E001-S01-B000"
    assert [b.type for b in beats[:4]] == [
        BeatType.CAMERA,
        BeatType.DIALOGUE,
        BeatType.ACTION,
        BeatType.DIALOGUE,
    ]
    starts = [b.start_s for b in ep.beats]
    assert starts == sorted(starts)
    assert len({b.id for b in ep.beats}) == len(ep.beats)


def test_dialogue_fields() -> None:
    ep = _ep(1)
    owen = ep.scenes[0].beats[1]
    assert owen.speaker == "OWEN"
    assert owen.emotion_tags == ["furious", "loud"]
    assert owen.text == "You stole the ledger, Marta!"
    assert (owen.start_s, owen.end_s) == (1.0, 3.5)


def test_voice_over_and_off_screen_flags() -> None:
    beats = _ep(1).scenes[0].beats
    vo = next(b for b in beats if b.voice_over)
    assert vo.speaker == "MARTA" and vo.emotion_tags == ["reflective", "hurt"]
    os_ = next(b for b in beats if b.off_screen)
    assert os_.speaker == "HARBORMASTER" and os_.emotion_tags == ["grave"]  # 'Continues' dropped


def test_messy_episode_never_crashes_and_warns() -> None:
    ep = _ep(2)
    c = codes(ep)
    for expected in (
        "orphan_text",
        "unknown_block_type",
        "malformed_timestamp",
        "beat_dropped",
        "end_inferred",
        "end_before_start",
        "empty_block",
    ):
        assert expected in c, expected
    assert "stray" in " ".join(w.message for w in ep.warnings if w.code == "orphan_text")


def test_messy_episode_keeps_good_beats() -> None:
    ep = _ep(2)
    assert ep.scenes[0].heading.startswith("INT. HARBORMASTER")  # upper-case labels
    texts = [b.text for b in ep.beats]
    assert "You will not like what the last page says." in texts  # curly quotes stripped
    assert "We have to burn it." not in texts  # bad start => dropped, never guessed
    assert "Burn it before she wakes." in texts  # bad end => kept with end=start
    burn = next(b for b in ep.beats if b.text.startswith("Burn"))
    assert burn.end_s == burn.start_s
    match = next(b for b in ep.beats if "match" in b.text)
    assert match.end_s == match.start_s  # end<start clamped
    assert "trailing garbage" not in " ".join(texts)  # after SCRIPT END
    assert {b.type for b in ep.beats if b.start_s in (5.0, 5.5)} == {BeatType.OTHER}


def test_unclosed_parenthetical_header() -> None:
    last = _ep(2).beats[-1]
    assert last.speaker == "MARTA" and last.emotion_tags == ["whispering"]


def test_no_markers_episode() -> None:
    ep = _ep(3)
    assert "missing_script_start" in codes(ep)
    assert len(ep.beats) == 2
    assert ep.duration_s == pytest.approx(4.0)


def test_crlf_and_nbsp_are_normalized() -> None:
    src = (FIX / "episode-001-Episode 9000001.txt").read_text()
    a = parse_episode(src, "episode-001-x.txt")
    messy = "\ufeff" + src.replace("\n", "\r\n").replace("stole the", "stole\u00a0the")
    b = parse_episode(messy, "x")
    assert [x.text for x in a.beats] == [x.text for x in b.beats]


def test_empty_and_garbage_input() -> None:
    ep = parse_episode("", "episode-007-foo.txt")
    assert ep.number == 7 and ep.beats == []
    assert "no_beats" in codes(ep)
    ep2 = parse_episode("lorem ipsum\n\x00\x01 nonsense ** not a block")
    assert ep2.beats == []


def test_episode_number_mismatch_warns_and_uses_header() -> None:
    src = "EPISODE NUMBER: Episode 5\n--- SCRIPT START ---\nSCENE 01\n"
    ep = parse_episode(src, "episode-002-x.txt")
    assert ep.number == 5 and "episode_number_mismatch" in codes(ep)


def test_normalize_speaker_only_cleans_case_and_space() -> None:
    assert normalize_speaker("  alexander's   mother ") == "ALEXANDER'S MOTHER"
    assert normalize_speaker("LADY JULIANA") == "LADY JULIANA"  # titles resolved show-wide


def test_loader_show_metadata_and_hash_stable() -> None:
    a, b = load_show(FIX), load_show(FIX)
    assert [e.number for e in a.episodes] == [1, 2, 3]
    assert a.slug == "synthetic-show"
    assert a.source_hash == b.source_hash and len(a.source_hash) == 64


def test_loader_errors(tmp_path: Path) -> None:
    with pytest.raises(IngestionError):
        load_show(tmp_path / "missing")
    with pytest.raises(IngestionError):
        load_show(tmp_path)  # empty dir


def test_loader_duplicates_and_oversize(tmp_path: Path) -> None:
    body = (
        "EPISODE NUMBER: Episode 1\n--- SCRIPT START ---\nSCENE 01\n"
        "**ACTION:**\nStart: (00:00.00) | End: (00:01.00)\nHi\n"
    )
    (tmp_path / "episode-001-a.txt").write_text(body)
    (tmp_path / "episode-001-b.txt").write_text(body)
    (tmp_path / "episode-002-big.txt").write_text(body + "x" * 500)
    show = load_show(tmp_path, max_file_bytes=300)
    assert [e.number for e in show.episodes] == [1]
    assert {w.code for w in show.warnings} == {
        "duplicate_episode",
        "file_too_large",
        "low_dialogue_ratio",  # the tiny fixture has no dialogue
    }


def test_loader_rejects_a_folder_with_no_screenplay_text(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello, these are my notes\n")
    with pytest.raises(IngestionError, match="no parseable episodes.*no timestamped"):
        load_show(tmp_path)


def test_loader_skips_a_stray_non_screenplay_file_and_warns(tmp_path: Path) -> None:
    (tmp_path / "README.txt").write_text("not a screenplay\n")
    for f in FIX.glob("*.txt"):
        (tmp_path / f.name).write_bytes(f.read_bytes())
    show = load_show(tmp_path)
    assert [e.number for e in show.episodes] == [1, 2, 3]
    assert "no_beats" in {w.code for w in show.warnings}
