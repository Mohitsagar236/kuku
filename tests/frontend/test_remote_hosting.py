"""Remote hosting (Render) must be opt-in, password-protected and fail closed."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from promo_finder.interfaces.web.app import create_app

HOST = "promo-finder.onrender.com"


def _auth(password: str, user: str = "editor") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PF_ALLOWED_HOSTS", HOST)
    monkeypatch.setenv("PF_VIEWER_PASSWORD", "s3cret-pass")
    (tmp_path / "outputs").mkdir()
    return TestClient(create_app(tmp_path / "outputs"), base_url=f"https://{HOST}")


def test_remote_requires_password(remote: TestClient) -> None:
    r = remote.get("/api/runs")
    assert r.status_code == 401
    assert "Basic" in r.headers["www-authenticate"]
    assert remote.get("/api/runs", headers=_auth("wrong")).status_code == 401
    assert remote.get("/api/runs", headers=_auth("s3cret-pass")).status_code == 200


def test_remote_health_check_is_public(remote: TestClient) -> None:
    assert remote.get("/readyz").status_code in {200, 503}
    assert remote.get("/readyz").status_code != 401


def test_remote_post_needs_password_and_same_origin(remote: TestClient) -> None:
    body = {"promo_id": "x", "verdict": "approve"}
    origin = {"Origin": f"https://{HOST}"}
    assert remote.post("/api/review?id=r", json=body, headers=origin).status_code == 401
    cross = {**_auth("s3cret-pass"), "Origin": "https://evil.example"}
    assert remote.post("/api/review?id=r", json=body, headers=cross).status_code == 403


def test_other_hosts_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PF_ALLOWED_HOSTS", HOST)
    monkeypatch.setenv("PF_VIEWER_PASSWORD", "pw")
    (tmp_path / "o").mkdir()
    client = TestClient(create_app(tmp_path / "o"), base_url="https://evil.example")
    assert client.get("/api/runs", headers=_auth("pw")).status_code == 403


def test_hosts_without_password_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PF_ALLOWED_HOSTS", HOST)
    monkeypatch.delenv("PF_VIEWER_PASSWORD", raising=False)
    (tmp_path / "o").mkdir()
    with (
        pytest.raises(ValueError, match="PF_VIEWER_PASSWORD"),
        TestClient(create_app(tmp_path / "o")),
    ):
        pass


def test_local_default_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PF_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("PF_VIEWER_PASSWORD", raising=False)
    (tmp_path / "o").mkdir()
    assert (
        TestClient(create_app(tmp_path / "o"), base_url="http://127.0.0.1")
        .get("/api/runs")
        .status_code
        == 200
    )
    assert (
        TestClient(create_app(tmp_path / "o"), base_url="https://x.onrender.com")
        .get("/api/runs")
        .status_code
        == 403
    )
