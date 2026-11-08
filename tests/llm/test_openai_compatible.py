"""openai_compatible provider against a real local HTTP server (a stub of /v1/chat/completions)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from promo_finder.infrastructure.llm import LLMAuthError, LLMConfigError, LLMRequest, LLMSettings
from promo_finder.infrastructure.llm.errors import LLMTransientError
from promo_finder.infrastructure.llm.factory import build_client, build_llm

REQ = LLMRequest(system="SYS", user="USER", task="score", max_tokens=64)
OK_BODY = {
    "model": "stub-model-2026",
    "choices": [{"message": {"content": '{"results": []}'}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}


class Stub:
    """Records every request; answers from a queue of (status, body) (default: 200 OK_BODY)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.replies: list[tuple[int, Any]] = []
        self.models: list[str] = ["stub-model", "other-model"]
        self.get_replies: list[tuple[int, Any]] = []  # overrides for GET /models
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                stub.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": json.loads(self.rfile.read(n)),
                    }
                )
                status, body = stub.replies.pop(0) if stub.replies else (200, OK_BODY)
                raw = (json.dumps(body) if not isinstance(body, str) else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:  # noqa: N802
                stub.requests.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}}
                )
                status, body = (
                    stub.get_replies.pop(0)
                    if stub.get_replies
                    else (200, {"data": [{"id": m} for m in stub.models]})
                )
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_a: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"


@pytest.fixture()
def stub() -> Iterator[Stub]:
    s = Stub()
    t = threading.Thread(target=s.server.serve_forever, daemon=True)
    t.start()
    yield s
    s.server.shutdown()
    s.server.server_close()


def settings(stub: Stub, tmp_path: Path, **kw: Any) -> LLMSettings:
    base = {
        "provider": "openai_compatible",
        "base_url": stub.url,
        "model": "stub-model",
        "api_key": "sk-test-123",
        "extra_headers": '{"X-Org": "kuku", "X-Route": "eu"}',
        "rpm": 0,
        "rpd": 0,
        "max_retries": 1,
        "cache_dir": str(tmp_path / "cache"),
    }
    return LLMSettings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


def test_request_shape_auth_extra_headers_and_response(stub: Stub, tmp_path: Path) -> None:
    llm = build_llm(settings(stub, tmp_path), persist=False)
    out = llm.complete(REQ)
    (r,) = stub.requests
    assert r["path"] == "/v1/chat/completions"
    assert r["headers"]["authorization"] == "Bearer sk-test-123"
    assert r["headers"]["x-org"] == "kuku" and r["headers"]["x-route"] == "eu"
    assert r["body"]["model"] == "stub-model" and r["body"]["max_tokens"] == 64
    assert r["body"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in r["body"]["messages"]] == ["system", "user"]
    assert (out.text, out.tokens_in, out.tokens_out) == ('{"results": []}', 11, 7)
    assert out.provider == "openai_compatible" and out.model == "stub-model-2026"


def test_no_api_key_sends_no_authorization_header(stub: Stub, tmp_path: Path) -> None:
    build_client(settings(stub, tmp_path, api_key="")).complete(REQ)
    assert "authorization" not in stub.requests[0]["headers"]


def test_gateway_quirks_are_adapted_once_then_remembered(stub: Stub, tmp_path: Path) -> None:
    stub.replies = [
        (400, {"error": "Unsupported parameter: 'max_tokens'; use max_completion_tokens"}),
        (400, {"error": "response_format json_object is not supported by this model"}),
    ]
    client = build_client(settings(stub, tmp_path))
    client.complete(REQ)
    client.complete(REQ)  # second call goes straight through with the adapted shape
    bodies = [r["body"] for r in stub.requests]
    assert len(bodies) == 4
    assert "max_tokens" in bodies[0] and "max_completion_tokens" in bodies[1]
    assert "response_format" in bodies[1] and "response_format" not in bodies[2]
    assert "max_completion_tokens" in bodies[3] and "response_format" not in bodies[3]


def test_http_errors_map_to_the_shared_taxonomy(stub: Stub, tmp_path: Path) -> None:
    client = build_client(settings(stub, tmp_path))
    stub.replies = [(401, {"error": "bad key"})]
    with pytest.raises(LLMAuthError):
        client.complete(REQ)
    stub.replies = [(503, "upstream overloaded")]
    with pytest.raises(LLMTransientError):
        client.complete(REQ)


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"base_url": ""}, "LLM_BASE_URL"),
        ({"model": ""}, "LLM_MODEL"),
        ({"extra_headers": "X-Org: kuku"}, "JSON object"),
        ({"extra_headers": '{"X-Retries": 3}'}, "string values"),
    ],
)
def test_misconfiguration_is_a_clear_config_error(
    stub: Stub, tmp_path: Path, kw: dict[str, str], msg: str
) -> None:
    with pytest.raises(LLMConfigError, match=msg):
        build_client(settings(stub, tmp_path, **kw))


def test_cli_reports_config_errors_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from promo_finder.interfaces.cli.__main__ import main

    fixture = Path(__file__).parents[1] / "fixtures" / "synthetic_show"
    monkeypatch.setenv("LLM_BASE_URL", "")
    rc = main(["run", str(fixture), "--provider", "openai_compatible", "--model", "m",
               "--out", str(tmp_path / "o"), "--no-cache"])  # fmt: skip
    assert rc == 2 and "needs LLM_BASE_URL" in capsys.readouterr().err
