"""The review sheet never invents ratings; compare only counts complete, valid reviews."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "review_sheet", Path(__file__).resolve().parents[2] / "scripts" / "review_sheet.py"
)
rs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rs)


def _run(tmp_path: Path, n: int = 6) -> Path:
    promos = [
        {
            "rank": i, "id": f"id{i}", "segments": [
                {"episode": i, "start_ts": "00:01.00", "end_ts": "00:31.00"}],
            "scores": {"hook": 7.0, "cliffhanger": 7.0},
            "hook": {"text": "a hook line"}, "cliffhanger": {"text": "a cliff line"},
        }
        for i in range(1, n + 1)
    ]  # fmt: skip
    (tmp_path / "promos.json").write_text(json.dumps({"promos": promos}))
    return tmp_path


def test_make_writes_a_blank_sheet_and_compare_reports_pending(tmp_path, capsys):
    run = _run(tmp_path)
    sheet = tmp_path / "s.csv"
    assert rs.make(run, 20, sheet) == 0
    rows = list(csv.DictReader(sheet.open()))
    assert len(rows) == 6 and all(r["hook_1to5"] == "" for r in rows)
    assert rs.compare(run, sheet) == 1
    assert "pending" in capsys.readouterr().out


def test_compare_uses_only_complete_valid_rows(tmp_path, capsys):
    run = _run(tmp_path)
    sheet = tmp_path / "s.csv"
    rs.make(run, 20, sheet)
    rows = list(csv.DictReader(sheet.open()))
    for i, r in enumerate(rows[:4]):  # synthetic test data only
        for k in rs.RATINGS:
            r[f"{k}_1to5"] = str(5 - i)
        r["verdict_accept_revise_reject"] = "accept" if i < 2 else "reject"
    rows[4]["hook_1to5"] = "9"  # invalid and incomplete: ignored
    with sheet.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rs.FIELDS)
        w.writeheader()
        w.writerows(rows)
    assert rs.compare(run, sheet) == 0
    out = capsys.readouterr().out
    assert "reviewed 4 promos" in out and "1.00" in out


def test_spearman_handles_ties_and_constants():
    assert rs.spearman([1, 2, 3], [1, 2, 3]) == 1.0
    assert rs.spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert rs.spearman([1, 1, 1], [1, 2, 3]) is None
