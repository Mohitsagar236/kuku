"""`doctor` preflight: pass/fail lines, no secrets, against a local stub server."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from promo_finder.infrastructure.llm import LLMSettings
from promo_finder.interfaces.cli.doctor import estimate_calls, run_doctor
from tests.e2e.helpers import write_show
from tests.llm.test_openai_compatible import Stub

SECRET = "sk-SUPERSECRET-0123456789"
HDR_SECRET = "hdr-value-do-not-print"


@pytest.fixture()
def stub() -> Iterator[Stub]:
    s = Stub()
    t = threading.Thread(target=s.server.serve_forever, daemon=True)
    t.start()
    yield s
    s.server.shutdown()
    s.server.server_close()


def cfg(stub: Stub, tmp: Path, **kw: str) -> LLMSettings:
    base = dict(provider="openai_compatible", base_url=stub.url, model="stub-model",
                api_key=SECRET, extra_headers=f'{{"X-Org": "{HDR_SECRET}"}}', rpm=0, rpd=5,
                cache_dir=str(tmp / "c"))  # fmt: skip
    return LLMSettings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


def run(settings: LLMSettings, show: Path | None, tmp: Path, **kw) -> tuple[int, str]:
    lines: list[str] = []
    rc = run_doctor(
        str(show) if show else None, settings, env_path=tmp / ".env", out=lines.append, **kw
    )
    return rc, "\n".join(lines)


def test_all_pass_and_nothing_secret_is_printed(stub: Stub, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"LLM_API_KEY={SECRET}\nLLM_PROVIDER=openai_compatible\n")
    show = write_show(tmp_path, n_eps=3)
    stub.replies = [(200, {"choices": [{"message": {"content": '{"ok": true, "n": 3}'}}],
                           "usage": {"prompt_tokens": 20, "completion_tokens": 8}})]  # fmt: skip
    rc, out = run(cfg(stub, tmp_path), show, tmp_path)
    assert rc == 0, out
    assert "PASS  .env found (2 settings)" in out
    assert "PASS  input parses: 3 episodes" in out
    assert "model 'stub-model' is listed (2 models)" in out
    assert "PASS  JSON-mode test call: valid JSON" in out
    assert "daily quota (client count): 1 of 5 used, 4 left" in out
    assert "RESULT: ready to run" in out
    for secret in (SECRET, HDR_SECRET):
        assert secret not in out
    assert "X-Org" in out and "values hidden" in out
    get = next(r for r in stub.requests if r["path"].startswith("/v1/models"))
    assert get["headers"]["authorization"] == f"Bearer {SECRET}"  # sent, never printed


def test_bad_key_fails_clearly(stub: Stub, tmp_path: Path) -> None:
    stub.get_replies = [(401, {"error": "no"})]
    rc, out = run(cfg(stub, tmp_path), None, tmp_path, call=False)
    assert rc == 1 and "FAIL  http://127.0.0.1" in out and "rejected the API key (HTTP 401)" in out
    assert SECRET not in out


def test_unknown_model_fails_and_suggests_real_ones(stub: Stub, tmp_path: Path) -> None:
    rc, out = run(cfg(stub, tmp_path, model="no-such-model"), None, tmp_path, call=False)
    assert rc == 1 and "model 'no-such-model' is not in its list; e.g. stub-model" in out


def test_gateway_without_a_model_list_warns_and_leaves_it_to_the_test_call(
    stub: Stub, tmp_path: Path
) -> None:
    stub.get_replies = [(404, {"error": "not found"})]
    stub.replies = [(200, {"choices": [{"message": {"content": "not json at all"}}], "usage": {}})]
    rc, out = run(cfg(stub, tmp_path), None, tmp_path)
    assert "WARN  http://127.0.0.1" in out and "no model list" in out
    assert rc == 1 and "FAIL  JSON-mode test call failed:" in out  # the bad reply is caught here


def test_unreachable_provider_fails(tmp_path: Path) -> None:
    s = LLMSettings(_env_file=None, provider="openai_compatible", model="m", api_key="k",
                    base_url="http://127.0.0.1:9/v1", rpm=0, rpd=0, cache_dir=str(tmp_path / "c"))  # type: ignore[arg-type]  # fmt: skip
    rc, out = run(s, None, tmp_path, call=False)
    assert rc == 1 and "cannot reach http://127.0.0.1:9" in out


def test_missing_settings_fail_before_any_network(tmp_path: Path) -> None:
    s = LLMSettings(_env_file=None, provider="openai_compatible", model="m", base_url="")  # type: ignore[arg-type]
    rc, out = run(s, None, tmp_path)
    assert rc == 1 and "FAIL  provider settings: " in out and "LLM_BASE_URL" in out
    assert "WARN  .env not found" in out


def test_bad_input_folder_fails(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    s = LLMSettings(_env_file=None, provider="mock")  # type: ignore[arg-type]
    rc, out = run(s, tmp_path / "x", tmp_path)
    assert rc == 1 and "FAIL  input folder:" in out and "SKIP  network checks" in out


def test_mock_provider_needs_no_network_and_reports_candidates(tmp_path: Path) -> None:
    show = write_show(tmp_path, n_eps=4)
    s = LLMSettings(_env_file=None, provider="mock")  # type: ignore[arg-type]
    rc, out = run(s, show, tmp_path)
    assert rc == 0 and "PASS  candidates:" in out and "RESULT: ready to run" in out


def test_quota_warning_when_the_show_needs_more_than_is_left(stub: Stub, tmp_path: Path) -> None:
    show = write_show(tmp_path, n_eps=8)
    stub.replies = [(200, {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}})]
    rc, out = run(cfg(stub, tmp_path, rpd=3), show, tmp_path)
    assert (
        "WARN  daily quota (client count): 1 of 3 used, 2 left: fewer than this show may need"
        in out
    )
    assert rc == 0  # a warning is not a failure


def test_call_estimate_mirrors_the_scorer_batching() -> None:
    # batches of 8 per episode: ceil(8/8)+ceil(9/8)+ceil(1/8) = 1+2+1 = 4; stitched in 5s: 2
    # clarity top min(60, 24)=24 in 6s = 4; rerank = 2 passes; coherence adds <= ceil(6/5)=2
    assert estimate_calls({1: 8, 2: 9, 3: 1}, n_stitched=6) == (4 + 2 + 4 + 2, 4 + 2 + 4 + 2 + 2)


def test_host_helper_never_echoes_paths_or_tokens() -> None:
    from promo_finder.interfaces.cli.doctor import _host, _secret

    assert (
        _host("https://gw.example.com:8443/v1/secret-path?token=abc")
        == "https://gw.example.com:8443"
    )
    assert _host("not a url") == "(no host)"
    assert _secret("abcdef") == "set (6 chars)" and _secret("") == "not set"
