from pathlib import Path

import pytest
from promo_common.llm import LLMRequest, LLMSettings, build_client, build_llm, load_prompt
from promo_common.llm.budget import Budget, Pricing
from promo_common.llm.cache import FileCache, MemoryCache, make_key
from promo_common.llm.errors import (
    BudgetExceeded,
    DailyQuotaExhausted,
    InvalidLLMOutput,
    LLMAuthError,
    LLMTransientError,
)
from promo_common.llm.json_utils import extract_json
from promo_common.llm.managed import ManagedLLM
from promo_common.llm.providers.gemini import GeminiProvider
from promo_common.llm.providers.mock import MockProvider
from promo_common.llm.ratelimit import RateLimiter


# ---- rate limiter ----------------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def test_rpm_is_enforced_with_waiting() -> None:
    c = Clock()
    rl = RateLimiter(rpm=2, clock=c.now, sleep=c.sleep)
    rl.acquire()
    rl.acquire()
    rl.acquire()  # third must wait for the first to leave the 60s window
    assert c.sleeps and c.t >= 60.0


def test_rpd_stops_and_persists_across_runs(tmp_path: Path) -> None:
    f = tmp_path / "q.json"
    rl = RateLimiter(rpd=3, state_path=f, today=lambda: "2026-11-07")
    for _ in range(3):
        rl.acquire()
    with pytest.raises(DailyQuotaExhausted):
        rl.acquire()
    # a NEW process the same day sees the used quota; the next day starts fresh
    assert RateLimiter(rpd=3, state_path=f, today=lambda: "2026-11-07").used_today == 3
    with pytest.raises(DailyQuotaExhausted):
        RateLimiter(rpd=3, state_path=f, today=lambda: "2026-11-07").acquire()
    RateLimiter(rpd=3, state_path=f, today=lambda: "2026-11-08").acquire()


def test_mark_exhausted_blocks_further_calls() -> None:
    rl = RateLimiter(rpd=100)
    rl.mark_exhausted()
    with pytest.raises(DailyQuotaExhausted):
        rl.acquire()


# ---- budget / pricing -----------------------------------------------------------------------
def test_budget_caps() -> None:
    b = Budget(max_usd=0.01)
    b.ensure()
    b.add(0.02)
    with pytest.raises(BudgetExceeded):
        b.ensure()
    c = Budget(max_calls=1)
    c.add(0)
    with pytest.raises(BudgetExceeded):
        c.ensure()


def test_pricing_cost_and_unknown_model(tmp_path: Path) -> None:
    f = tmp_path / "p.yaml"
    f.write_text("models:\n  m: {in: 1.0, out: 5.0}\n")
    p = Pricing.load(f)
    assert p.cost("m", 1_000_000, 200_000) == pytest.approx(2.0) and p.known("m")
    assert p.cost("other", 10, 10) == 0.0 and not p.known("other")
    assert Pricing.load(tmp_path / "missing.yaml").table == {}


# ---- cache ----------------------------------------------------------------------------------
def test_file_cache_roundtrip_and_miss(tmp_path: Path) -> None:
    c = FileCache(tmp_path)
    k = make_key("a", "b")
    assert c.get(k) is None
    c.set(k, {"x": 1})
    assert FileCache(tmp_path).get(k) == {"x": 1}
    assert make_key("a", "b") != make_key("ab", "")  # separator prevents collisions
    m = MemoryCache()
    m.set("k", {"y": 2})
    assert m.get("k") == {"y": 2}


# ---- json -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! {"a": 1} hope it helps']
)
def test_extract_json_variants(raw: str) -> None:
    assert extract_json(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["", "nothing here", '{"a": ', "[1, 2"])
def test_extract_json_failures(raw: str) -> None:
    with pytest.raises(InvalidLLMOutput):
        extract_json(raw)


# ---- managed client ---------------------------------------------------------------------------
def _managed(provider: MockProvider, **kw) -> tuple[ManagedLLM, list[float]]:
    sleeps: list[float] = []
    return ManagedLLM(provider, sleep=sleeps.append, backoff_base_s=2.0, **kw), sleeps


def test_transient_errors_are_retried_with_exponential_backoff() -> None:
    llm, sleeps = _managed(MockProvider(faults=["transient", "rate_limit", "transient"]))
    resp = llm.complete(LLMRequest("s", "u", "score", meta={"candidates": []}))
    assert resp.provider == "mock" and llm.records[0].attempts == 4
    assert len(sleeps) == 3 and sleeps[0] >= 2.0 and sleeps[2] >= 8.0  # 2s, (retry-after 0), 8s


def test_gives_up_after_max_retries() -> None:
    llm, _ = _managed(MockProvider(faults=["transient"] * 10), max_retries=2)
    with pytest.raises(LLMTransientError):
        llm.complete(LLMRequest("s", "u", "score", meta={"candidates": []}))


def test_daily_quota_propagates_and_blocks_future_calls() -> None:
    llm, _ = _managed(MockProvider(faults=["daily_quota"]), limiter=RateLimiter(rpd=50))
    with pytest.raises(DailyQuotaExhausted):
        llm.complete(LLMRequest("s", "u", "score", meta={"candidates": []}))
    with pytest.raises(DailyQuotaExhausted):  # client now refuses without calling the provider
        llm.complete(LLMRequest("s", "u", "score", meta={"candidates": []}))


def test_usage_cost_and_raw_capture(tmp_path: Path) -> None:
    llm, _ = _managed(
        MockProvider(), pricing=Pricing({"mock-1": {"in": 1e6, "out": 1e6}}), raw_dir=tmp_path
    )
    llm.complete(LLMRequest("system text", "user text", "clarity", meta={"candidates": []}))
    s = llm.summary()
    assert s["calls"] == 1 and s["cost_usd"] > 0 and s["price_known"] and "clarity" in s["by_task"]
    raw = list(tmp_path.glob("*_clarity.json"))
    assert len(raw) == 1 and "user text" in raw[0].read_text()


def test_budget_cap_stops_calls() -> None:
    llm, _ = _managed(MockProvider(), budget=Budget(max_calls=1))
    llm.complete(LLMRequest("s", "u", "score", meta={"candidates": []}))
    with pytest.raises(BudgetExceeded):
        llm.complete(LLMRequest("s", "u", "score", meta={"candidates": []}))


# ---- factory / settings / prompts --------------------------------------------------------------
def test_factory_switches_provider_by_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-xyz")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c = build_client(LLMSettings(_env_file=None))
    assert isinstance(c, GeminiProvider) and c.model == "gemini-xyz"
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert build_client(LLMSettings(_env_file=None)).provider == "mock"


def test_factory_missing_key_names_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(LLMAuthError, match="GEMINI_API_KEY"):
        build_client(LLMSettings(provider="gemini", _env_file=None))
    with pytest.raises(ValueError, match="unknown LLM_PROVIDER"):
        build_client(LLMSettings(provider="nope", _env_file=None))


def test_build_llm_default_is_mock_and_in_memory() -> None:
    llm = build_llm(LLMSettings(_env_file=None), persist=False)
    assert llm.provider == "mock" and isinstance(llm.cache, MemoryCache)


def test_prompts_load_render_and_version(tmp_path: Path) -> None:
    for name in ("score_v1", "clarity_v1", "coherence_v1", "rerank_v1"):
        p = load_prompt(name)
        assert p.version == "v1" and p.system and len(p.sha) == 12
    p = load_prompt("clarity_v1")
    s, u = p.render(candidates="CLIP TEXT", retry_note="")
    assert "CLIP TEXT" in u and "{{" not in u
    (tmp_path / "bad_v1.md").write_text("no sections")
    with pytest.raises(ValueError):
        load_prompt("bad_v1", tmp_path)


def test_quota_file_name_keeps_dotted_model_names_apart(tmp_path) -> None:
    """Regression: with_suffix() chopped 'gemini-3.5-flash-lite' to 'gemini-3', merging models."""

    def limiter_path(model: str) -> Path:
        s = LLMSettings(provider="ollama", model=model, cache_dir=str(tmp_path), _env_file=None)
        path = build_llm(s).limiter._path
        assert path is not None
        return path

    a, b = limiter_path("m-3.5-flash-lite"), limiter_path("m-3.1-flash-lite")
    assert a != b
    assert a.name == "quota_ollama_m-3.5-flash-lite.json"
