"""Format drift a new show might have. The parser must degrade, never crash."""

from ingestion_service import parse_episode
from promo_common.schemas import BeatType

BASE = """EPISODE NUMBER: Episode 4
EPISODE LOGLINE: A logline.
CONTINUITY NOTES: Some notes.
--- SCRIPT START ---
SCENE 01
Scene heading: INT. ROOM – DAY
Scene start: 00:00.00
Scene end: 00:30.00
Actions and dialogue:

**ACTION:**
Start: (00:01.00) | End: (00:03.00)
Ben slams the door.

**BEN:**
Start: (00:03.50) | End: (00:05.00)
"Where is she?"

**ANA (Whispering):**
Start: (00:05.50) | End: (00:07.00)
"Gone."
--- SCRIPT END ---
"""


def codes(ep) -> set[str]:
    return {w.code for w in ep.warnings}


def test_baseline_is_clean() -> None:
    ep = parse_episode(BASE, "episode-004-x.txt")
    assert ep.number == 4 and len(ep.beats) == 3 and codes(ep) == set()


def test_dialogue_without_parentheticals() -> None:
    ben = next(b for b in parse_episode(BASE).beats if b.speaker == "BEN")
    assert ben.type is BeatType.DIALOGUE and ben.emotion_tags == []
    assert ben.text == "Where is she?"


def test_hour_minute_second_timestamps() -> None:
    src = BASE.replace("(00:03.50) | End: (00:05.00)", "(0:00:03.50) | End: (0:00:05.00)")
    ep = parse_episode(src)
    ben = next(b for b in ep.beats if b.speaker == "BEN")
    assert (ben.start_s, ben.end_s) == (3.5, 5.0) and "malformed_timestamp" not in codes(ep)


def test_crlf_whole_file_identical_structure() -> None:
    a = parse_episode(BASE)
    b = parse_episode(BASE.replace("\n", "\r\n"))
    assert [(x.id, x.type, x.text, x.start_s) for x in a.beats] == [
        (x.id, x.type, x.text, x.start_s) for x in b.beats
    ]
    assert a.logline == b.logline and a.continuity_notes == b.continuity_notes


def test_missing_logline_and_continuity_notes() -> None:
    src = BASE.replace("EPISODE LOGLINE: A logline.\n", "").replace(
        "CONTINUITY NOTES: Some notes.\n", ""
    )
    ep = parse_episode(src)
    assert ep.logline == "" and ep.continuity_notes == "" and len(ep.beats) == 3


def test_extra_unknown_block_type_is_kept_as_other() -> None:
    src = BASE.replace("**BEN:**", "**MONTAGE:**").replace(
        '"Where is she?"', "Quick cuts of the town."
    )
    ep = parse_episode(src)
    montage = next(b for b in ep.beats if b.text.startswith("Quick cuts"))
    assert montage.type is BeatType.OTHER and "unknown_block_type" in codes(ep)


def test_missing_script_end_still_parses_everything() -> None:
    ep = parse_episode(BASE.replace("--- SCRIPT END ---\n", ""))
    assert len(ep.beats) == 3 and "missing_script_end" in codes(ep)


def test_missing_scene_times_derive_from_beats() -> None:
    src = BASE.replace("Scene start: 00:00.00\n", "").replace("Scene end: 00:30.00\n", "")
    sc = parse_episode(src).scenes[0]
    assert (sc.start_s, sc.end_s) == (1.0, 7.0)
