import json

import httpx
import pytest
from promo_common.llm import LLMRequest
from promo_common.llm.errors import (
    DailyQuotaExhausted,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
)
from promo_common.llm.providers.anthropic import AnthropicProvider
from promo_common.llm.providers.gemini import GeminiProvider
from promo_common.llm.providers.ollama import OllamaProvider
from promo_common.llm.providers.openai_compat import GroqProvider, OpenAICompatProvider

REQ = LLMRequest(system="SYS", user="USER", task="t", max_tokens=50)


def transport(handler):
    return httpx.MockTransport(handler)


def test_openai_request_and_response() -> None:
    seen = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = (
            str(r.url),
            r.headers["authorization"],
            json.loads(r.content),
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"a":1}'}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                "model": "m1",
            },
        )

    p = OpenAICompatProvider("m1", "KEY", transport=transport(h))
    out = p.complete(REQ)
    assert out.text == '{"a":1}' and (out.tokens_in, out.tokens_out) == (11, 3)
    assert seen["url"].endswith("/v1/chat/completions") and seen["auth"] == "Bearer KEY"
    assert seen["body"]["temperature"] == 0.0 and seen["body"]["response_format"] == {
        "type": "json_object"
    }
    assert seen["body"]["max_completion_tokens"] == 50


def test_groq_uses_its_base_url_and_max_tokens() -> None:
    seen = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(r.url), json.loads(r.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    GroqProvider("llama", "K", transport=transport(h)).complete(REQ)
    assert "api.groq.com" in seen["url"] and seen["body"]["max_tokens"] == 50


def test_anthropic_request_and_response() -> None:
    seen = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["h"], seen["body"] = r.headers, json.loads(r.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "model": "c",
            },
        )

    out = AnthropicProvider("c", "AK", transport=transport(h)).complete(REQ)
    assert out.text == "hi" and (out.tokens_in, out.tokens_out) == (5, 2)
    assert seen["h"]["x-api-key"] == "AK" and "anthropic-version" in seen["h"]
    assert seen["body"]["system"] == "SYS" and seen["body"]["messages"][0]["content"] == "USER"


def test_gemini_request_response_and_thinking_tokens() -> None:
    seen = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = (
            str(r.url),
            r.headers["x-goog-api-key"],
            json.loads(r.content),
        )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "x", "thought": True}, {"text": '{"ok":1}'}]}}
                ],
                "usageMetadata": {
                    "promptTokenCount": 20,
                    "candidatesTokenCount": 4,
                    "thoughtsTokenCount": 6,
                },
            },
        )

    g = GeminiProvider("gemini-test", "GK", transport=transport(h))
    g.thinking_budget = 0
    out = g.complete(REQ)
    assert out.text == '{"ok":1}'  # thought parts are not part of the answer
    assert out.tokens_out == 10  # thinking tokens are billed as output
    assert "models/gemini-test:generateContent" in seen["url"] and seen["key"] == "GK"
    cfg = seen["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["thinkingConfig"] == {
        "thinkingBudget": 0
    }
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"


def test_ollama_request_and_response() -> None:
    def h(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content)
        assert body["format"] == "json" and body["stream"] is False
        return httpx.Response(
            200, json={"message": {"content": "{}"}, "prompt_eval_count": 7, "eval_count": 2}
        )

    out = OllamaProvider("gemma3:1b", None, transport=transport(h)).complete(REQ)
    assert (out.tokens_in, out.tokens_out) == (7, 2)


@pytest.mark.parametrize(
    ("status", "body", "exc"),
    [
        (
            429,
            "RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProjectPerModel-FreeTier",
            DailyQuotaExhausted,
        ),
        (429, "slow down", LLMRateLimitError),
        (401, "bad key", LLMAuthError),
        (403, "forbidden", LLMAuthError),
        (500, "oops", LLMTransientError),
        (503, "unavailable", LLMTransientError),
        (400, "invalid argument", LLMError),
    ],
)
def test_http_errors_are_mapped(status: int, body: str, exc: type[Exception]) -> None:
    p = OpenAICompatProvider(
        "m", "K", transport=transport(lambda r: httpx.Response(status, text=body))
    )
    with pytest.raises(exc):
        p.complete(REQ)


def test_retry_after_header_is_read() -> None:
    p = OpenAICompatProvider("m", "K", transport=transport(
        lambda r: httpx.Response(429, text="x", headers={"retry-after": "7"})))  # fmt: skip
    with pytest.raises(LLMRateLimitError) as e:
        p.complete(REQ)
    assert e.value.retry_after == 7.0


def test_timeout_and_connection_errors_are_transient() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t")

    with pytest.raises(LLMTransientError):
        OpenAICompatProvider("m", "K", transport=transport(boom)).complete(REQ)

    def refused(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("c")

    with pytest.raises(LLMTransientError):
        OpenAICompatProvider("m", "K", transport=transport(refused)).complete(REQ)


def test_missing_api_key_is_an_auth_error() -> None:
    with pytest.raises(LLMAuthError):
        OpenAICompatProvider(
            "m", None, transport=transport(lambda r: httpx.Response(200))
        ).complete(REQ)


def test_temperature_rejected_by_model_is_retried_without_it() -> None:
    bodies = []

    def h(r: httpx.Request) -> httpx.Response:
        b = json.loads(r.content)
        bodies.append(b)
        if "temperature" in b:
            return httpx.Response(400, text="Unsupported value: 'temperature' does not support 0")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    p = OpenAICompatProvider("m", "K", transport=transport(h))
    assert p.complete(REQ).text == "ok"
    assert "temperature" in bodies[0] and "temperature" not in bodies[1]
    p.complete(REQ)  # remembered: no more wasted first attempt
    assert "temperature" not in bodies[2]


def test_gemini_drops_thinking_config_if_model_rejects_it() -> None:
    bodies = []

    def h(r: httpx.Request) -> httpx.Response:
        b = json.loads(r.content)
        bodies.append(b["generationConfig"])
        if "thinkingConfig" in b["generationConfig"]:
            return httpx.Response(400, text="Unknown name thinkingConfig: use thinkingLevel")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}],
                                         "usageMetadata": {}})  # fmt: skip

    g = GeminiProvider("gemini-new", "K", transport=transport(h))
    g.thinking_budget = 0
    assert g.complete(REQ).text == "{}"
    assert "thinkingConfig" in bodies[0] and "thinkingConfig" not in bodies[1]


def test_factory_disables_gemini_thinking_for_flash_but_not_pro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from promo_common.llm import LLMSettings, build_client

    for var in ("LLM_THINKING_BUDGET",):
        monkeypatch.delenv(var, raising=False)
    flash = build_client(
        LLMSettings(provider="gemini", model="gemini-2.5-flash", gemini_api_key="k", _env_file=None)
    )
    pro = build_client(
        LLMSettings(provider="gemini", model="gemini-2.5-pro", gemini_api_key="k", _env_file=None)
    )
    assert flash.thinking_budget == 0 and pro.thinking_budget is None
