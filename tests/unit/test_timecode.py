import pytest
from promo_common.timecode import TimecodeError, format_timecode, iou, overlap, parse_timecode


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00:00.00", 0.0),
        ("00:04.80", 4.8),
        ("02:18.00", 138.0),
        (" (01:16.50) ", 76.5),
        ("1:02:03.5", 3723.5),
        ("01:05", 65.0),
    ],
)
def test_parse_ok(raw: str, expected: float) -> None:
    assert parse_timecode(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "bad", "xx:yy", "00:75.00", "-1:00.00", "12", "00:00.00.00"])
def test_parse_malformed(raw: str) -> None:
    with pytest.raises(TimecodeError):
        parse_timecode(raw)


def test_format_roundtrip() -> None:
    for s in (0.0, 4.8, 59.99, 138.0, 200.75):
        assert parse_timecode(format_timecode(s)) == pytest.approx(s, abs=0.005)
    assert format_timecode(138.0) == "02:18.00"


def test_format_negative() -> None:
    with pytest.raises(ValueError):
        format_timecode(-1)


def test_overlap_and_iou() -> None:
    assert overlap(0, 10, 5, 15) == 5
    assert overlap(0, 5, 5, 10) == 0
    assert iou(0, 10, 0, 10) == 1.0
    assert iou(0, 10, 5, 15) == pytest.approx(5 / 15)
    assert iou(0, 10, 20, 30) == 0.0
    assert iou(0, 0, 0, 0) == 0.0
