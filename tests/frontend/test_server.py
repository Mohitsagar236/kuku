"""The local viewer's server: real HTTP over a socket, against a temp outputs/ folder."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promo_finder.infrastructure.runs import RunRepository
from promo_finder.interfaces.web.app import create_app


def _run(d: Path, model: str, n: int, partial: bool = False) -> None:
    d.mkdir(parents=True)
    promos = [
        {
            "id": f"p{i}",
            "rank": i + 1,
            "kind": "single",
            "segments": [
                {
                    "episode": 1,
                    "start_s": 0.0,
                    "end_s": 30.0,
                    "start_ts": "00:00.00",
                    "end_ts": "00:30.00",
                }
            ],
            "total_duration_s": 30.0,
            "hook": {
                "beat_id": "b1",
                "episode": 1,
                "ts": "00:00.00",
                "kind": "dialogue",
                "text": "Synthetic opening",
            },
            "cliffhanger": {
                "beat_id": "b2",
                "episode": 1,
                "ts": "00:29.00",
                "kind": "dialogue",
                "text": "Synthetic ending",
            },
            "why": "Synthetic rationale",
            "scores": {
                "hook": 7.0,
                "cliffhanger": 7.0,
                "clarity": 7.0,
                "intensity": 7.0,
                "rule_hook": 7.0,
                "rule_cliffhanger": 7.0,
                "rule_intensity": 7.0,
                "rule_clarity": 7.0,
            },
            "rule_score": 7.0,
            "final_score": 7.0,
            "confidence": 0.8,
        }
        for i in range(n)
    ]
    body = {"show": "Demo", "generated_at": d.name, "model": model, "partial": partial}
    (d / "promos.json").write_text(json.dumps({**body, "promos": promos}))
    (d / "promos_ablation_x.json").write_text(json.dumps({**body, "promos": promos[:1]}))
    (d / "promos.csv").write_text("rank,episode\n1,3\n")
    (d / "quality_report.md").write_text("# Report\n")
    (d / "run_metadata.json").write_text("{}")
    (d / "llm_raw").mkdir()
    (d / "llm_raw" / "x.json").write_text("{}")


@pytest.fixture()
def outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "outputs"
    _run(out / "demo" / "20260101-000000", "gemini/x", 3)
    _run(out / "demo" / "smoke-20260102-000000", "gemini/x", 1)
    (out / "demo" / "rules_baseline").mkdir()
    (out / "demo" / "rules_baseline" / "promos.json").write_text(
        json.dumps({"show": "Demo", "model": "rules", "promos": []})
    )
    (tmp_path / "secret.json").write_text('{"promos": []}')
    return out


@pytest.fixture()
def port(outputs: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(outputs), base_url="http://127.0.0.1:8765", raise_server_exceptions=False
    ) as client:
        yield client


def get(port: TestClient, path: str, host: str | None = None) -> tuple[int, dict[str, str], bytes]:
    response = port.get(path, headers={"Host": host} if host else {})
    return response.status_code, response.headers, response.content


def test_lists_runs_newest_first_with_kind(port: int) -> None:
    code, _, body = get(port, "/api/runs")
    runs = json.loads(body)
    assert code == 200
    assert [r["kind"] for r in runs] == ["smoke", "full", "baseline"]
    assert runs[1]["count"] == 3 and runs[1]["show"] == "Demo"


def test_loads_a_run_with_variants_report_and_downloads(port: int) -> None:
    code, _, body = get(port, "/api/run?id=demo/20260101-000000")
    run = json.loads(body)
    assert code == 200
    assert set(run["variants"]) == {"main", "ablation_x"}
    assert run["report"] == "# Report\n" and run["downloads"] == ["promos.csv"]


@pytest.mark.parametrize(
    "rid",
    ["", "demo", "../secret.json", "demo/../../", "/etc", "demo/20260101-000000/llm_raw", "a\x00b"],
)
def test_unknown_or_escaping_run_ids_are_404(port: int, rid: str) -> None:
    from urllib.parse import quote

    code, _, body = get(port, f"/api/run?id={quote(rid)}")
    assert code == 404 and json.loads(body)["error"]


def test_only_whitelisted_files_download(port: int) -> None:
    ok = get(port, "/api/file?id=demo/20260101-000000&name=promos.csv")
    assert ok[0] == 200 and ok[2].startswith(b"rank,")
    assert "attachment" in ok[1]["Content-Disposition"]
    for name in ("run_metadata.json", "llm_raw/x.json", "../../secret.json", "ranking.md"):
        assert get(port, f"/api/file?id=demo/20260101-000000&name={name}")[0] == 404


def test_static_files_only_and_security_headers(port: int) -> None:
    code, headers, body = get(port, "/")
    assert code == 200 and b'id="root"' in body
    assert (
        headers["X-Content-Type-Options"] == "nosniff"
        and "frame-ancestors" in headers["Content-Security-Policy"]
    )
    for path in ("/server.py", "/../server.py", "/%2e%2e/.env", "/app.js/../server.py"):
        assert get(port, path)[0] == 404


def test_foreign_host_header_is_refused(port: int) -> None:
    """DNS rebinding: a web page served from evil.example must not read the confidential outputs."""
    assert get(port, "/api/runs", host="evil.example:8765")[0] == 403
    assert get(port, "/api/runs", host="localhost:8765")[0] == 200


def test_corrupt_promos_file_is_a_json_error_not_a_dropped_connection(
    port: int, outputs: Path
) -> None:
    (outputs / "demo" / "20260101-000000" / "promos_ablation_x.json").write_text("{not json")
    code, _, body = get(port, "/api/run?id=demo/20260101-000000")
    assert code == 500 and "corrupt" in json.loads(body)["error"]
    (outputs / "demo" / "20260101-000000" / "promos.json").write_text("{not json")
    code, _, body = get(port, "/api/runs")  # the broken run is skipped, the rest still list
    assert code == 200 and len(json.loads(body)) == 2


def test_missing_outputs_folder_lists_nothing(tmp_path: Path) -> None:
    assert RunRepository(tmp_path / "nope").list_runs() == []


def test_health_and_security_headers(port: TestClient) -> None:
    response = port.get("/healthz")
    assert response.status_code == 200 and response.json()["status"] == "ok"
    assert response.headers["x-request-id"]
    assert "unpkg" not in response.headers["content-security-policy"]


def test_apps_do_not_share_repository_state(outputs: Path, tmp_path: Path) -> None:
    first = TestClient(create_app(outputs), base_url="http://localhost")
    second = TestClient(create_app(tmp_path / "empty"), base_url="http://localhost")
    assert len(first.get("/api/runs").json()) == 3
    assert second.get("/api/runs").json() == []


def test_symlinked_artifacts_are_never_served(
    port: TestClient, outputs: Path, tmp_path: Path
) -> None:
    run = outputs / "demo" / "20260101-000000"
    secret = tmp_path / "private.md"
    secret.write_text("secret")
    (run / "ranking.md").symlink_to(secret)
    (run / "quality_report.md").unlink()
    (run / "quality_report.md").symlink_to(secret)
    assert port.get("/api/file?id=demo/20260101-000000&name=ranking.md").status_code == 404
    assert port.get("/api/run?id=demo/20260101-000000").json()["report"] is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"promos": None},
        {"promos": 0},
        {"promos": [None]},
        {"promos": [], "generated_at": 42},
        {"promos": [], "generated_at": {}},
        {"promos": [], "show": []},
        {"promos": [], "model": {}},
    ],
)
def test_malformed_artifact_shape_does_not_disable_other_runs(
    port: TestClient, outputs: Path, payload: object
) -> None:
    """Valid JSON can still be an invalid run; isolate it just like truncated JSON."""
    artifact = outputs / "demo" / "20260101-000000" / "promos.json"
    artifact.write_text(json.dumps(payload))
    response = port.get("/api/runs")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert all(run["id"] != "demo/20260101-000000" for run in response.json())
    selected = port.get("/api/run?id=demo/20260101-000000")
    assert selected.status_code == 500
    assert "corrupt" in selected.json()["error"]


@pytest.mark.parametrize("field,value", [("segments", []), ("hook", None), ("confidence", 2)])
def test_malformed_promo_is_isolated(port: TestClient, outputs: Path, field, value) -> None:
    artifact = outputs / "demo" / "20260101-000000" / "promos.json"
    payload = json.loads(artifact.read_text())
    payload["promos"][0][field] = value
    artifact.write_text(json.dumps(payload))
    assert len(port.get("/api/runs").json()) == 2
    assert port.get("/api/run?id=demo/20260101-000000").status_code == 500


def test_malformed_variant_returns_corrupt_artifact_error(port: TestClient, outputs: Path) -> None:
    artifact = outputs / "demo" / "20260101-000000" / "promos_ablation_x.json"
    artifact.write_text('{"promos": [null]}')
    assert len(port.get("/api/runs").json()) == 3
    response = port.get("/api/run?id=demo/20260101-000000")
    assert response.status_code == 500 and "corrupt" in response.json()["error"]
