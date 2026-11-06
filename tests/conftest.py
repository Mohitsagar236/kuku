import pytest
from candidate_service import generate_candidates, load_config, load_lexicon
from candidate_service.config import CandidateConfig, Lexicon
from promo_common.schemas import Candidate, Show
from scoring_service import ScoringConfig, load_scoring_config

from tests.candidate.conftest import make_show
from tests.shared import story_episode


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
