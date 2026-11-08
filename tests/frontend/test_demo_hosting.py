"""The public demo is seeded offline and cannot expose local private outputs."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promo_finder.interfaces.web.app import create_app


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PF_DEMO_MODE", "true")
    monkeypatch.setenv("PF_DEMO_ROOT", str(tmp_path / "public-demo"))
    monkeypatch.setenv("PF_ALLOWED_HOSTS", "promo-demo.onrender.com")
    monkeypatch.delenv("PF_VIEWER_PASSWORD", raising=False)
    # A caller-supplied directory must never become public in demo mode.
    private = tmp_path / "private-outputs"
    run = private / "secret-show" / "secret-run"
    run.mkdir(parents=True)
    (run / "promos.json").write_text('{"promos": []}')
    return TestClient(create_app(private), base_url="https://promo-demo.onrender.com")


def test_public_demo_has_sample_data_without_password(demo: TestClient) -> None:
    assert demo.get("/").status_code == 200
    runs = demo.get("/api/runs").json()
    assert runs and all(run["model"] in {"mock/render-demo", "rules-only (no LLM)"} for run in runs)
    assert not any("secret-show" in run["id"] for run in runs)
    run = next(run for run in runs if run["model"] == "mock/render-demo")
    data = demo.get("/api/run", params={"id": run["id"]}).json()
    assert data["variants"]["main"]["promos"]
    assert demo.get("/api/run?id=secret-show/secret-run").status_code == 404


def test_demo_reviews_and_cuts_work_with_correct_origin(demo: TestClient) -> None:
    run = next(run for run in demo.get("/api/runs").json() if run["model"] == "mock/render-demo")
    data = demo.get("/api/run", params={"id": run["id"]}).json()
    promo = data["variants"]["main"]["promos"][0]
    origin = {"Origin": "https://promo-demo.onrender.com"}
    response = demo.post(
        "/api/review",
        params={"id": run["id"]},
        headers=origin,
        json={"promo_id": promo["id"], "verdict": "approve", "note": "Sample review"},
    )
    assert response.status_code == 200
    feedback = demo.get("/api/feedback", params={"id": run["id"]}).json()
    assert feedback["can_nudge"]
    assert feedback["entries"][promo["id"]]["verdict"] == "approve"
    assert (
        demo.post(
            "/api/review",
            params={"id": run["id"]},
            headers={"Origin": "https://evil.example"},
            json={"promo_id": promo["id"], "verdict": "reject"},
        ).status_code
        == 403
    )


def test_demo_seed_is_idempotent(demo: TestClient, tmp_path: Path) -> None:
    before = demo.get("/api/runs").json()
    second = TestClient(
        create_app(tmp_path / "another-private-path"), base_url="https://promo-demo.onrender.com"
    )
    assert second.get("/api/runs").json() == before
