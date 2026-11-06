from ingestion_service.speakers import build_alias_map, resolve_speakers
from promo_common.schemas import Beat, BeatType, Episode, Scene


def _ep(*names: str) -> Episode:
    beats = [
        Beat(id=f"b{i}", episode_number=1, scene_idx=1, seq=i, type=BeatType.DIALOGUE,
             speaker=n, speaker_raw=n, text="x", start_s=i, end_s=i + 0.5)
        for i, n in enumerate(names)
    ]  # fmt: skip
    return Episode(number=1, source_file="f", duration_s=10,
                   scenes=[Scene(idx=1, start_s=0, end_s=10, beats=beats)])  # fmt: skip


def test_single_titled_variant_merges_into_existing_bare_name() -> None:
    assert build_alias_map({"LADY JULIANA", "JULIANA", "ALEXANDER"}) == {"LADY JULIANA": "JULIANA"}


def test_two_different_titles_never_merge() -> None:
    # LORD ASHCROFT and LADY ASHCROFT are different people, even if bare ASHCROFT exists
    assert build_alias_map({"LORD ASHCROFT", "LADY ASHCROFT", "ASHCROFT"}) == {}
    assert build_alias_map({"LORD ASHCROFT", "LADY ASHCROFT"}) == {}


def test_no_bare_name_means_no_merge() -> None:
    assert build_alias_map({"LADY JULIANA", "ALEXANDER"}) == {}


def test_role_labels_and_lone_titles_are_untouched() -> None:
    assert build_alias_map({"THE FORMER GROOM", "GROOM", "LORD", "LADY IN GOLD GOWN"}) == {}


def test_resolve_speakers_applies_in_place_and_keeps_raw() -> None:
    ep = _ep("LADY JULIANA", "JULIANA", "LORD ASHCROFT", "LADY ASHCROFT", "ASHCROFT")
    aliases = resolve_speakers([ep])
    assert aliases == {"LADY JULIANA": "JULIANA"}
    got = [b.speaker for b in ep.beats]
    assert got == ["JULIANA", "JULIANA", "LORD ASHCROFT", "LADY ASHCROFT", "ASHCROFT"]
    assert ep.beats[0].speaker_raw == "LADY JULIANA"


def test_merge_is_show_wide_across_episodes() -> None:
    e1, e2 = _ep("DUKE ROLAND"), _ep("ROLAND")
    e2.number = 2
    assert resolve_speakers([e1, e2]) == {"DUKE ROLAND": "ROLAND"}
    assert e1.beats[0].speaker == "ROLAND"
