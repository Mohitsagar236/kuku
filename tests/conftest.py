import pytest

from promo_finder.candidates import generate_candidates, load_config, load_lexicon
from promo_finder.candidates.config import CandidateConfig, Lexicon
from promo_finder.domain.models import Candidate, Show
from promo_finder.scoring import ScoringConfig, load_scoring_config
from tests.candidate.conftest import make_show
from tests.shared import story_episode


@pytest.fixture(autouse=True)
def _never_call_a_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The author's .env holds a real key and provider. Tests must never reach it."""
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "")
    for var in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session")
def cfg() -> CandidateConfig:
    return load_config()


@pytest.fixture(scope="session")
def lex() -> Lexicon:
    return load_lexicon()


@pytest.fixture()
def show() -> Show:
    return make_show(
        story_episode(1, "ZZLOGLINE_ONE a baker is exiled", "ZZNOTES_ONE first episode"),
        story_episode(2, "ZZLOGLINE_TWO the baker returns", "ZZNOTES_TWO picks up directly"),
        story_episode(3, "ZZLOGLINE_THREE a duel is called", "ZZNOTES_THREE continues"),
    )


@pytest.fixture()
def cands(show: Show, cfg: CandidateConfig, lex) -> list[Candidate]:
    c = cfg.model_copy(update={"top_n_per_episode": 4, "stitch_top_n_total": 6})
    out, _ = generate_candidates(show, c, lex)
    assert any(x.kind == "stitched" for x in out) and len(out) > 8
    return out


@pytest.fixture()
def scfg() -> ScoringConfig:
    return load_scoring_config()
