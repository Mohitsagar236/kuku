"""The local viewer's server: real HTTP over a socket, against a temp outputs/ folder."""

from __future__ import annotations

import http.client
import importlib.util
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "viewer_server", Path(__file__).resolve().parents[2] / "frontend" / "server.py"
)
assert SPEC and SPEC.loader
srv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(srv)


def _run(d: Path, model: str, n: int, partial: bool = False) -> None:
    d.mkdir(parents=True)
    promos = [{"id": f"p{i}", "rank": i + 1} for i in range(n)]
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
    monkeypatch.setattr(srv, "OUTPUTS", out.resolve())
    return out


@pytest.fixture()
def port(outputs: Path) -> Iterator[int]:
    s = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s.server_address[1]
    s.shutdown()
    s.server_close()


def get(port: int, path: str, host: str | None = None) -> tuple[int, dict[str, str], bytes]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


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
    assert get(port, "/api/runs", host=f"localhost:{port}")[0] == 200


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
    assert srv.list_runs(tmp_path / "nope") == []


def test_port_in_use_is_a_clean_error(port: int, capsys: pytest.CaptureFixture[str]) -> None:
    assert srv.main(["--port", str(port)]) == 2
    assert "cannot listen" in capsys.readouterr().err
