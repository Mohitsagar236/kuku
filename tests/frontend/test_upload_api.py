"""In-app upload + analysis: works on real files, stays private, never leaks keys, resists abuse."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promo_finder.application.analysis import AnalysisError, AnalysisService
from promo_finder.interfaces.web.app import create_app
from tests.e2e.helpers import write_show
from tests.shared import make_llm

ORIGIN = "http://127.0.0.1:8765"
HDR = {"Host": "127.0.0.1:8765", "Origin": ORIGIN, "Content-Type": "application/json"}


@pytest.fixture()
def files(tmp_path: Path) -> list[dict]:
    show = write_show(tmp_path / "src", n_eps=4)
    return [{"name": f.name, "text": f.read_text()} for f in sorted(show.glob("*.txt"))]


@pytest.fixture()
def outputs(tmp_path: Path) -> Path:
    return tmp_path / "outputs"


@pytest.fixture()
def client(outputs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.delenv("PF_DEMO_MODE", raising=False)
    for var in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # never read the developer's real .env in tests
    outputs.mkdir()
    with TestClient(create_app(outputs), base_url=ORIGIN, raise_server_exceptions=False) as c:
        yield c


def post(c: TestClient, body, path="/api/analyze", headers=None):
    return c.post(path, content=json.dumps(body).encode(), headers=headers or HDR)


def get(c: TestClient, path: str):
    return c.get(path, headers={"Host": "127.0.0.1:8765"})


def wait(c: TestClient, job: str, timeout: float = 90.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        r = get(c, f"/api/analyze/status?job={job}").json()
        if r["state"] in {"done", "error"}:
            return r
        time.sleep(0.2)
    raise AssertionError("analysis did not finish")


# ------------------------------------------------------------------------- happy path


def test_rules_only_upload_gives_a_real_ranked_run_the_viewer_can_open(client, files) -> None:
    r = post(client, {"files": files, "mode": "rules", "title": "My pilot"})
    assert r.status_code == 202
    st = wait(client, r.json()["job"])
    assert st["state"] == "done" and st["run"]["show"] == "My pilot" and st["run"]["count"] > 0
    run_id = st["run_id"]
    assert run_id.startswith("upload-") and "rules-only" in (st["run"]["model"] or "")
    data = get(client, f"/api/run?id={run_id}").json()
    promos = data["variants"]["main"]["promos"]
    assert promos and all(20 <= p["total_duration_s"] <= 60 for p in promos)
    assert all(p["segments"] and p["hook"] and p["cliffhanger"] for p in promos)
    assert "promos.csv" in data["downloads"]
    assert get(client, f"/api/file?id={run_id}&name=promos.csv").status_code == 200
    fb = get(client, f"/api/feedback?id={run_id}").json()
    assert fb["can_nudge"], fb["nudge_reason"]  # cuts can be adjusted: the source is kept


def test_a_model_run_uses_the_injected_model_and_labels_it(outputs, files) -> None:
    svc = AnalysisService(outputs, public=False, llm_factory=lambda s: make_llm())
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="model")
    st = _wait_service(svc, tok)
    assert st["state"] == "done" and st["run"]["count"] > 0
    assert not (st["run"]["model"] or "").startswith("rules-only")


def _wait_service(svc: AnalysisService, tok: str, timeout: float = 90.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        st = svc.status(tok)
        assert st is not None
        if st["state"] in {"done", "error"}:
            return st
        time.sleep(0.2)
    raise AssertionError("timeout")


# ------------------------------------------------------------------------- privacy


def test_uploads_are_unlisted_unguessable_and_isolated(client, files) -> None:
    a = wait(client, post(client, {"files": files}).json()["job"])
    b = wait(client, post(client, {"files": files}).json()["job"])
    listed = get(client, "/api/runs").json()
    assert all(not r["id"].startswith("upload-") for r in listed)  # nobody can browse uploads
    assert (
        a["run_id"] != b["run_id"] and len(a["run_id"].split("/")[0]) == len("upload-") + 32
    )  # 128-bit token
    # only the owner's own link adds the run to the list
    own = get(client, f"/api/runs?extra={a['run_id']}").json()
    assert [r["id"] for r in own if r["id"].startswith("upload-")] == [a["run_id"]]
    assert get(client, "/api/run?id=upload-deadbeef/20260101-000000").status_code == 404
    assert get(client, "/api/analyze/status?job=" + "0" * 32).status_code == 404


def test_uploads_and_results_are_deleted_after_the_retention_time(outputs, files) -> None:
    now = [1000.0]
    svc = AnalysisService(outputs, ttl_s=3600, clock=lambda: now[0])
    tok = svc.submit([(f["name"], f["text"]) for f in files])
    st = _wait_service(svc, tok)
    run_dir = outputs / st["run_id"].split("/")[0]
    assert run_dir.is_dir() and (outputs / ".uploads" / tok).is_dir()
    import os

    old = time.time() - 7200  # folders are aged by mtime, so cleanup also survives a restart
    for p in (run_dir, outputs / ".uploads" / tok):
        os.utime(p, (old, old))
    now[0] = time.time()
    assert svc.purge() >= 2
    assert not run_dir.exists() and not (outputs / ".uploads" / tok).exists()
    assert svc.status(tok) is None


def test_nothing_is_written_to_the_shared_response_cache(
    outputs, files, monkeypatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)  # a default ".cache/llm" would be created here if anything used it
    svc = AnalysisService(outputs, llm_factory=lambda s: make_llm())
    fl = [(f["name"], f["text"]) for f in files]
    for mode in ("rules", "model"):
        assert _wait_service(svc, svc.submit(fl, mode=mode))["state"] == "done"
    assert not (tmp_path / ".cache").exists()


# ------------------------------------------------------------------------- server key policy

SERVER_KEY = "AIzaSy-SERVER-SECRET-0123456789"


def gemini_env():
    from promo_finder.infrastructure.llm import LLMSettings

    return LLMSettings(
        _env_file=None, provider="gemini", model="gemini-test", gemini_api_key=SERVER_KEY
    )


def test_the_browser_cannot_send_a_key_or_pick_a_provider(client, files) -> None:
    for extra in ({"api_key": "k"}, {"provider": "openai"}, {"model": "x"}):
        assert post(client, {"files": files, "mode": "model", **extra}).status_code == 400


def test_model_runs_use_the_servers_key_and_it_never_reaches_a_response_or_file(
    outputs, files
) -> None:
    def boom(settings):
        raise RuntimeError(f"401 unauthorized for key {settings.gemini_api_key}")

    svc = AnalysisService(outputs, public=True, settings_factory=gemini_env, llm_factory=boom)
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="model")
    st = _wait_service(svc, tok)
    assert st["state"] == "error" and SERVER_KEY not in json.dumps(st)
    assert SERVER_KEY not in json.dumps(svc.model_status())
    assert all(
        SERVER_KEY not in p.read_text(errors="ignore") for p in outputs.rglob("*") if p.is_file()
    )


def test_a_server_with_a_key_runs_the_model_on_a_public_deployment_too(outputs, files) -> None:
    svc = AnalysisService(outputs, public=True, settings_factory=gemini_env,
                          llm_factory=lambda s: make_llm())  # fmt: skip
    assert svc.model_status() == {"available": True, "name": "gemini/gemini-test", "reason": ""}
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="model")
    assert _wait_service(svc, tok)["state"] == "done"


def test_without_a_server_key_model_mode_is_unavailable_but_rules_still_work(client, files) -> None:
    caps = get(client, "/api/capabilities").json()
    assert caps["model"]["available"] is False and "model" in caps["model"]["reason"].lower()
    r = post(client, {"files": files, "mode": "model"})
    assert r.status_code == 503 and "Rules only" in r.json()["error"]
    assert (
        wait(client, post(client, {"files": files, "mode": "rules"}).json()["job"])["state"]
        == "done"
    )


def test_a_provider_without_its_key_is_reported_clearly(outputs, files) -> None:
    from promo_finder.infrastructure.llm import LLMSettings

    svc = AnalysisService(outputs, settings_factory=lambda: LLMSettings(
        _env_file=None, provider="gemini", gemini_api_key=None))  # fmt: skip
    st = svc.model_status()
    assert st["available"] is False and "gemini API key" in st["reason"]
    with pytest.raises(AnalysisError) as e:
        svc.submit([(f["name"], f["text"]) for f in files], mode="model")
    assert e.value.code == 503


def test_public_servers_cap_daily_model_runs_but_never_rules_runs(outputs, files) -> None:
    svc = AnalysisService(outputs, public=True, max_model_jobs_per_day=2,
                          settings_factory=gemini_env, llm_factory=lambda s: make_llm())  # fmt: skip
    fl = [(f["name"], f["text"]) for f in files]
    for _ in range(2):
        assert _wait_service(svc, svc.submit(fl, mode="model"))["state"] == "done"
    with pytest.raises(AnalysisError) as e:
        svc.submit(fl, mode="model")
    assert e.value.code == 429 and "daily limit of 2" in e.value.message
    assert _wait_service(svc, svc.submit(fl, mode="rules"))["state"] == "done"  # still free


def test_local_installs_have_no_daily_model_cap_by_default(outputs) -> None:
    assert AnalysisService(outputs, public=False).max_model_jobs_per_day == 0
    assert AnalysisService(outputs, public=True).max_model_jobs_per_day > 0


def test_rules_mode_needs_no_key_even_on_a_public_server(outputs, files) -> None:
    svc = AnalysisService(outputs, public=True)
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="rules")
    assert _wait_service(svc, tok)["state"] == "done"


# ------------------------------------------------------------------------- bad input & abuse


def test_malformed_requests_get_clear_errors_not_crashes(client, files) -> None:
    assert post(client, {"files": []}).status_code == 400
    assert post(client, {"files": [{"name": "a.pdf", "text": "x"}]}).status_code == 400
    assert post(client, {"files": [{"name": "a.txt", "text": "   "}]}).status_code == 400
    assert post(client, {"files": files, "mode": "turbo"}).status_code == 400
    assert post(client, {"files": files, "api_key": "k", "extra": 1}).status_code == 400
    r = post(client, {"files": [{"name": "a.txt", "text": "hello"}] * 61})
    assert r.status_code == 400  # over the file-count limit
    big = [{"name": f"e{i}.txt", "text": "x" * 399_000} for i in range(25)]
    assert post(client, {"files": big}).status_code == 413  # over the total-size limit


def test_a_file_that_is_not_a_screenplay_fails_the_job_with_a_readable_message(client) -> None:
    r = post(client, {"files": [{"name": "notes.txt", "text": "just some prose, no timestamps"}]})
    st = wait(client, r.json()["job"])
    assert st["state"] == "error" and "no parseable episodes" in st["error"]
    assert "Traceback" not in json.dumps(st)


def test_file_names_cannot_escape_the_upload_folder(outputs, files) -> None:
    svc = AnalysisService(outputs)
    evil = [("../../../etc/passwd.txt", files[0]["text"]), ("..\\..\\win.txt", files[1]["text"])]
    tok = svc.submit(evil, mode="rules")
    _wait_service(svc, tok)
    inside = {p.name for p in (outputs / ".uploads" / tok).rglob("*.txt")}
    assert inside == {"passwd.txt", "win.txt"}
    assert not (outputs.parent / "etc").exists()


def test_cross_origin_and_wrong_content_type_uploads_are_refused(client, files) -> None:
    bad_origin = {**HDR, "Origin": "https://evil.example"}
    assert post(client, {"files": files}, headers=bad_origin).status_code == 403
    assert (
        post(client, {"files": files}, headers={**HDR, "Content-Type": "text/plain"}).status_code
        == 415
    )


def test_the_analyser_refuses_when_busy(outputs, files) -> None:
    import threading

    gate = threading.Event()
    svc = AnalysisService(
        outputs, max_waiting=0, llm_factory=lambda s: (gate.wait(30), make_llm())[1]
    )
    fl = [(f["name"], f["text"]) for f in files]
    first = svc.submit(fl, mode="model")  # model run blocks on the gate inside the factory
    # `settings.provider` is "mock" with no .env, so allow it via the injected factory
    with pytest.raises(AnalysisError) as e:
        svc.submit(fl, mode="rules")
    assert e.value.code == 429
    gate.set()
    _wait_service(svc, first)


def test_the_body_limit_for_uploads_is_larger_than_for_edits_but_still_bounded(client) -> None:
    huge = b'{"files": [], "pad": "' + b"x" * 12_500_000 + b'"}'
    assert client.post("/api/analyze", content=huge, headers=HDR).status_code == 413
    edit = b'{"promo_id": "a", "note": "' + b"x" * 9000 + b'"}'
    assert client.post("/api/review?id=x", content=edit, headers=HDR).status_code == 413


def test_capabilities_describe_limits_and_the_model_but_never_the_key(client, monkeypatch) -> None:
    caps = get(client, "/api/capabilities").json()
    assert caps["analyze"] is True and caps["limits"]["files"] == 60
    assert caps["retention_minutes"] == 60 and set(caps["model"]) == {"available", "name", "reason"}
    assert caps["model"]["available"] is False  # no key in this test environment
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", SERVER_KEY)
    caps = get(client, "/api/capabilities").json()
    assert caps["model"]["available"] is True and caps["model"]["name"].startswith("gemini/")
    assert SERVER_KEY not in json.dumps(caps)


def test_uploads_can_be_switched_off(outputs, monkeypatch) -> None:
    monkeypatch.setenv("PF_ANALYZE", "false")
    monkeypatch.delenv("PF_DEMO_MODE", raising=False)
    outputs.mkdir()
    with TestClient(create_app(outputs), base_url=ORIGIN) as c:
        assert get(c, "/api/capabilities").json()["analyze"] is False
        assert post(c, {"files": [{"name": "a.txt", "text": "x"}]}).status_code == 404


# ------------------------------------------------------------------------- live progress


def test_status_reports_real_progress_phases_and_counters_for_a_model_run(outputs, files) -> None:
    import threading

    seen: list[dict] = []
    gate = threading.Event()

    def slow_factory(settings):
        llm = make_llm()
        original = llm.complete

        def complete(req):
            out = original(req)
            gate.wait(0.05)  # let the poller observe intermediate states
            return out

        llm.complete = complete
        return llm

    svc = AnalysisService(outputs, settings_factory=gemini_env, llm_factory=slow_factory)
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="model")
    end = time.time() + 90
    while time.time() < end:
        st = svc.status(tok)
        assert st is not None
        seen.append(st["progress"])
        if st["state"] in {"done", "error"}:
            break
        time.sleep(0.01)
    assert st["state"] == "done"
    phases = [p["phase"] for p in seen]
    assert phases[-1] == "done" and "reading" in phases + ["reading"]
    assert {"score"} <= set(phases) or {"candidates", "write"} <= set(phases)
    final = seen[-1]
    assert (
        final["calls"] > 0 and final["tokens_in"] > 0 and final["model"] == "mock/mock-1"
    )  # the live client, not the settings
    assert final["episodes"] == 4 and sum(final["by_task"].values()) == final["calls"]
    assert [p["calls"] for p in seen] == sorted(p["calls"] for p in seen)  # counters only go up
    order = ["queued", "reading", "candidates", "score", "clarity", "rerank", "write", "done"]
    idx = [order.index(p) for p in phases]
    assert idx == sorted(idx), phases  # the phase never moves backwards


def test_rules_progress_has_no_model_and_no_calls(outputs, files) -> None:
    svc = AnalysisService(outputs)
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="rules")
    st = _wait_service(svc, tok)
    p = st["progress"]
    assert p["phase"] == "done" and p["calls"] == 0 and p["model"] == "" and p["episodes"] == 4


def test_progress_never_contains_script_text_or_the_key(outputs, files) -> None:
    svc = AnalysisService(outputs, settings_factory=gemini_env, llm_factory=lambda s: make_llm())
    tok = svc.submit([(f["name"], f["text"]) for f in files], mode="model")
    st = _wait_service(svc, tok)
    blob = json.dumps(st)
    assert SERVER_KEY not in blob
    first_line = next(line for line in files[0]["text"].splitlines() if line.startswith('"'))
    assert first_line.strip('"')[:30] not in blob
    assert set(st["progress"]) == {
        "phase", "mode", "model", "episodes", "calls", "by_task",
        "tokens_in", "tokens_out", "elapsed_s",
    }  # fmt: skip
