import json
import logging

import pytest
from ingestion_service.__main__ import main
from promo_common.config import Settings
from promo_common.errors import IngestionError, NotFound
from promo_common.logging import JsonFormatter, job_id_var, request_id_var
from promo_common.schemas import Beat, BeatType

from tests.unit.test_parser import FIX


def test_json_logging_carries_ids_and_extras() -> None:
    request_id_var.set("req-1")
    job_id_var.set("job-9")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "hello %s", ("w",), None)
    rec.stage = "parse"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "hello w" and out["request_id"] == "req-1"
    assert out["job_id"] == "job-9" and out["stage"] == "parse"


def test_problem_detail() -> None:
    p = NotFound("show 3").to_problem("rid")
    assert (p.status, p.title, p.detail, p.request_id) == (404, "Not found", "show 3", "rid")
    assert IngestionError("x").status == 422


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PF_LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_beat_rejects_end_before_start() -> None:
    with pytest.raises(ValueError):
        Beat(
            id="b",
            episode_number=1,
            scene_idx=1,
            seq=0,
            type=BeatType.ACTION,
            text="x",
            start_s=5,
            end_s=4,
        )


def test_cli_prints_stats_without_script_text(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["prog", str(FIX)]) == 0
    out = capsys.readouterr().out
    assert "episodes=3" in out and "malformed_timestamp" in out
    assert "ledger" not in out
    assert main(["prog"]) == 2
