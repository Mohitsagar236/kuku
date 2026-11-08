"""Viewer write endpoints: verdicts and cut nudges, over a real socket on a real pipeline run."""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promo_finder.application.models import RunOptions
from promo_finder.application.pipeline import run_pipeline
from promo_finder.evaluation import load_golden
from promo_finder.infrastructure.artifacts import write_all
from promo_finder.infrastructure.llm import LLMSettings
from promo_finder.interfaces.web.app import create_app
from tests.e2e.helpers import write_show
from tests.shared import make_llm


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    show_dir = write_show(tmp_path, n_eps=6)
    out = tmp_path / "outputs"
    opts = RunOptions(out_root=out, persist_cache=False, ablation=False, stability=False)
    res = run_pipeline(show_dir, opts, LLMSettings(_env_file=None), llm=make_llm())
    run_path = write_all(res)
    promos = json.loads((run_path / "promos.json").read_text())["promos"]
    assert len(promos) >= 4
    return {"outputs": out,"show_dir": show_dir, "run_id": run_path.relative_to(out).as_posix(),
            "run_path": run_path, "fb": run_path.parent / "editor_feedback.yaml",
            "promos": promos, "tmp": tmp_path}  # fmt: skip


@pytest.fixture()
def port(env: dict) -> Iterator[TestClient]:
    with TestClient(
        create_app(env["outputs"]), base_url="http://127.0.0.1:8765", raise_server_exceptions=False
    ) as client:
        yield client


def call(
    port: TestClient,
    method: str,
    path: str,
    body=None,
    *,
    origin="auto",
    ctype="application/json",
    host=None,
    raw: bytes | None = None,
    headers: dict | None = None,
):
    h = {"Host": host or "127.0.0.1:8765"}
    if method == "POST":
        if origin == "auto":
            h["Origin"] = "http://127.0.0.1:8765"
        elif origin:
            h["Origin"] = origin
        if ctype:
            h["Content-Type"] = ctype
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    response = port.request(method, path, content=data, headers=h)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.content


def rv(env: dict, promo: int, **body):
    return {"promo_id": env["promos"][promo]["id"], **body}


# ------------------------------------------------------------------------------ happy paths


def test_empty_state_then_a_review_is_saved_in_a_golden_readable_file(env, port) -> None:
    code, st = call(port, "GET", f"/api/feedback?id={env['run_id']}")
    assert code == 200 and st["entries"] == {} and st["can_nudge"] and st["nudge_reason"] == ""
    assert st["file"] == f"outputs/{env['run_path'].parent.name}/editor_feedback.yaml"
    assert st["refresh_command"] == f"make feedback RUN=outputs/{env['run_id']}"

    code, st = call(port, "POST", f"/api/review?id={env['run_id']}",
                    rv(env, 0, verdict="would_post", note="strong opening"))  # fmt: skip
    pid = env["promos"][0]["id"]
    assert code == 200 and st["entries"][pid]["verdict"] == "would_post"
    assert st["entries"][pid]["note"] == "strong opening" and not st["entries"][pid]["nudged"]
    assert st["summary"]["would_post"] == 1 and st["summary"]["top_positive"] == 1
    (g,) = load_golden(env["fb"])  # the EXISTING golden loader reads what the viewer wrote
    assert g.note == "strong opening"
    assert g.segments[0].episode == env["promos"][0]["segments"][0]["episode"]


def test_approve_reject_clear_and_notes_update_the_summary(env, port) -> None:
    url = f"/api/review?id={env['run_id']}"
    call(port, "POST", url, rv(env, 0, verdict="approve"))
    call(port, "POST", url, rv(env, 1, verdict="reject", note="spoils the twist"))
    code, st = call(port, "POST", url, rv(env, 2, note="check the ending"))  # note, no verdict
    s = st["summary"]
    assert (s["approved"], s["rejected"], s["entries"]) == (1, 1, 2)
    assert s["top_positive"] == 1 and s["top_rejected"] == 1 and s["precision_at_k"] > 0
    assert st["entries"][env["promos"][2]["id"]]["verdict"] is None
    code, st = call(port, "POST", url, rv(env, 1, verdict="clear"))
    assert st["summary"]["rejected"] == 0
    assert st["entries"][env["promos"][1]["id"]]["note"] == "spoils the twist"  # note survives
    text = env["fb"].read_text()
    assert "rejected: []" in text and "approve" in text


def test_nudge_is_refused_with_reasons_and_suggestions_then_accepted_on_a_suggestion(
    env, port
) -> None:
    pid = env["promos"][0]["id"]
    url = f"/api/nudge?id={env['run_id']}"
    code, err = call(port, "POST", url, rv(env, 0, segment=0, edge="end", delta=0.5))
    assert code == 422 and err["reasons"] and err["suggestions"]
    assert "not allowed" in err["error"] and not env["fb"].exists()  # nothing saved
    pick = err["suggestions"][0]
    code, st = call(port, "POST", url, rv(env, 0, segment=0, edge="end", to=pick["t"]))
    assert code == 200
    e = st["entries"][pid]
    assert e["nudged"] and e["segments"][0]["end_ts"] == pick["ts"]
    assert e["original"][0]["end_s"] == env["promos"][0]["segments"][0]["end_s"]
    # the saved cut is what the golden scorer would compare against
    code, st = call(port, "POST", f"/api/review?id={env['run_id']}", rv(env, 0, verdict="approve"))
    (g,) = load_golden(env["fb"])
    assert g.segments[0].end_s == pytest.approx(pick["t"], abs=0.006)
    code, st = call(port, "POST", url, rv(env, 0, reset=True))
    assert not st["entries"][pid]["nudged"]
    assert st["entries"][pid]["segments"][0]["end_s"] == env["promos"][0]["segments"][0]["end_s"]


def test_nudging_needs_the_screenplay_but_reviewing_does_not(env, port) -> None:
    shutil.move(env["show_dir"], env["tmp"] / "moved")
    code, st = call(port, "GET", f"/api/feedback?id={env['run_id']}")
    assert st["can_nudge"] is False and "was not found" in st["nudge_reason"]
    code, err = call(
        port, "POST", f"/api/nudge?id={env['run_id']}", rv(env, 0, segment=0, edge="end", delta=0.5)
    )
    assert code == 409 and "not found" in err["error"]
    code, st = call(port, "POST", f"/api/review?id={env['run_id']}", rv(env, 0, verdict="approve"))
    assert code == 200 and st["summary"]["approved"] == 1


def test_a_changed_screenplay_disables_nudges(env, port) -> None:
    ep = next(env["show_dir"].glob("*.txt"))
    ep.write_text(ep.read_text() + "\n")
    code, st = call(port, "GET", f"/api/feedback?id={env['run_id']}")
    assert st["can_nudge"] is False and "changed" in st["nudge_reason"]


def test_runs_without_a_source_path_explain_how_to_enable_nudges(env, port) -> None:
    meta = env["run_path"] / "run_metadata.json"
    m = json.loads(meta.read_text())
    del m["source_dir"]
    meta.write_text(json.dumps(m))
    code, st = call(port, "GET", f"/api/feedback?id={env['run_id']}")
    assert st["can_nudge"] is False and "re-run" in st["nudge_reason"]


def test_concurrent_reviews_are_all_kept(env, port) -> None:
    url = f"/api/review?id={env['run_id']}"
    errs: list = []

    def go(i: int) -> None:
        code, _ = call(port, "POST", url, rv(env, i, verdict="approve", note=f"n{i}"))
        if code != 200:
            errs.append(code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    code, st = call(port, "GET", f"/api/feedback?id={env['run_id']}")
    assert st["summary"]["approved"] == 4 and len(load_golden(env["fb"])) == 4


# ------------------------------------------------------------------------------ hostile input


def attempt(env: dict, port: int, expect: int, path="/api/review", body="default", **kw) -> None:
    body = rv(env, 0, verdict="approve") if body == "default" else body
    code, _ = call(port, "POST", f"{path}?id={env['run_id']}", body, **kw)
    assert code == expect, (kw, code)
    assert not env["fb"].exists(), f"a refused request must not write ({kw})"


def test_cross_site_and_malformed_posts_are_refused_and_write_nothing(env, port) -> None:
    attempt(env, port, 403, origin=None)  # a script without an Origin (or a non-browser)
    attempt(env, port, 403, origin="https://evil.example")  # another site's page
    attempt(env, port, 403, origin="http://localhost:8765")  # right machine, wrong origin string
    # DNS rebinding: the attacker's page is "same origin" with its own host name, so Origin
    # matches Host and ONLY the Host allow-list stops it
    attempt(env, port, 403, host="evil.example:8765", origin="http://evil.example:8765")
    attempt(env, port, 415, ctype="text/plain")  # the "simple request" CSRF trick
    attempt(env, port, 415, ctype="application/x-www-form-urlencoded")
    attempt(env, port, 415, ctype=None)
    attempt(env, port, 400, raw=b"{not json", body=None)
    attempt(env, port, 400, body=["a", "list"])
    attempt(env, port, 400, body={"verdict": "approve"})  # no promo id
    attempt(env, port, 400, body=rv(env, 0))  # neither verdict nor note
    attempt(env, port, 400, body=rv(env, 0, verdict="maybe"))
    attempt(env, port, 400, body=rv(env, 0, verdict=1))
    attempt(env, port, 400, body=rv(env, 0, verdict="approve", file="/etc/passwd"))  # unknown field
    attempt(env, port, 400, body=rv(env, 0, note="x" * 1001))
    attempt(env, port, 404, body={"promo_id": "../../etc/passwd", "verdict": "approve"})
    attempt(env, port, 404, body={"promo_id": "nope", "verdict": "approve"})
    attempt(env, port, 404, path="/api/delete")


def test_oversized_and_unlengthed_bodies_are_refused(env, port) -> None:
    attempt(env, port, 413, raw=b"x" * 9000, body=None)
    attempt(env, port, 411, headers={"Content-Length": ""}, raw=b"", body=None)


@pytest.mark.parametrize(
    "body",
    [
        {"segment": "0", "edge": "end", "delta": 0.5},
        {"segment": 0, "edge": "middle", "delta": 0.5},
        {"segment": 0, "edge": "end"},
        {"segment": 0, "edge": "end", "delta": 0.5, "to": 3.0},
        {"segment": 0, "edge": "end", "delta": True},
        {"segment": 0, "edge": "end", "delta": "0.5"},
        {"segment": True, "edge": "end", "delta": 0.5},
        {"segment": 9, "edge": "end", "delta": 0.5},
        {"segment": 0, "edge": "end", "delta": 0.5, "path": "../x"},
    ],
)
def test_bad_nudge_requests_are_400_and_write_nothing(env, port, body: dict) -> None:
    code, err = call(port, "POST", f"/api/nudge?id={env['run_id']}", rv(env, 0, **body))
    assert code == 400 and "error" in err and not env["fb"].exists()


def test_run_ids_cannot_escape_outputs(env, port) -> None:
    for rid in ("../outputs", "..", "/etc", f"{env['run_id']}/..", "x\x00y"):
        from urllib.parse import quote

        code, _ = call(port, "POST", f"/api/review?id={quote(rid)}", rv(env, 0, verdict="approve"))
        assert code == 404
        code, _ = call(port, "GET", f"/api/feedback?id={quote(rid)}")
        assert code == 404
    assert not env["fb"].exists()


def test_a_malformed_feedback_file_is_never_overwritten(env, port) -> None:
    env["fb"].write_text("promos: [broken: [")
    before = env["fb"].read_text()
    code, err = call(port, "POST", f"/api/review?id={env['run_id']}", rv(env, 0, verdict="approve"))
    assert code == 409 and "malformed" in err["error"]
    code, err = call(port, "GET", f"/api/feedback?id={env['run_id']}")
    assert code == 409
    assert env["fb"].read_text() == before


def test_the_only_file_ever_written_is_the_feedback_file(env, port) -> None:
    def snapshot() -> dict:
        return {p: p.stat().st_mtime_ns for p in env["tmp"].rglob("*") if p.is_file()}

    before = snapshot()
    call(port, "POST", f"/api/review?id={env['run_id']}", rv(env, 0, verdict="approve", note="x"))
    changed = {p for p, t in snapshot().items() if before.get(p) != t}
    assert changed == {env["fb"]}


def test_source_changed_after_a_successful_read_disables_nudges(env, port) -> None:
    url = f"/api/feedback?id={env['run_id']}"
    assert call(port, "GET", url)[1]["can_nudge"]
    ep = next(env["show_dir"].glob("*.txt"))
    ep.write_text(ep.read_text() + "\n")
    assert not call(port, "GET", url)[1]["can_nudge"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_cut_points_never_write(env, port, value) -> None:
    code, body = call(
        port,
        "POST",
        f"/api/nudge?id={env['run_id']}",
        rv(env, 0, segment=0, edge="end", delta=value),
    )
    assert code == 400 and not env["fb"].exists()


def _process_review(outputs: str, run_id: str, promo_id: str, result) -> None:
    from promo_finder.application.editor import EditorService
    from promo_finder.infrastructure.runs import RunRepository

    try:
        editor = EditorService(RunRepository(Path(outputs)))
        editor.post_review(run_id, {"promo_id": promo_id, "verdict": "approve"})
        result.put(None)
    except Exception as exc:
        result.put(type(exc).__name__)


def test_reviews_from_separate_processes_do_not_lose_updates(env) -> None:
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    workers = [
        context.Process(
            target=_process_review,
            args=(
                str(env["outputs"]),
                env["run_id"],
                p["id"],
                result,
            ),
        )
        for p in env["promos"][:4]
    ]
    for worker in workers:
        worker.start()
    try:
        for worker in workers:
            worker.join(timeout=15)
            assert worker.exitcode == 0
        assert [result.get(timeout=2) for _ in workers] == [None] * 4
        assert len(load_golden(env["fb"])) == 4
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        result.close()
