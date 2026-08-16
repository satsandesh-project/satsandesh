import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.main import app
from app.models import User


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_me_without_token_returns_401(client):
    response = client.get("/me")
    assert response.status_code == 401


def test_me_with_token_returns_user(client):
    response = client.get("/me", headers={"Authorization": "Bearer faketoken"})
    assert response.status_code == 200
    body = response.json()
    assert "id" in body
    assert "name" in body
    assert "preferred_language" in body
    assert "role" in body


def test_protected_route_rejects_wrong_role(client):
    # /moderator-only requires role="moderator". The stub user in get_current_user
    # is hardcoded to role="elder", so an authenticated-but-wrong-role caller must
    # get 403 (authorization failure), not 401 (authentication failure) — those are
    # different failure modes and callers need to be able to tell them apart.
    response = client.get("/moderator-only", headers={"Authorization": "Bearer faketoken"})
    assert response.status_code == 403


def test_dependency_override_swaps_current_user(client):
    # Demonstrates that get_current_user is a swappable seam: overriding it here
    # (via app.dependency_overrides, FastAPI's supported DI-swap mechanism) is the
    # same mechanism next week's real-JWT implementation will use in production —
    # only the function body changes, not each route.
    def fake_moderator_user():
        return User(id="override-1", name="Override Mod", preferred_language="en", role="moderator")

    app.dependency_overrides[get_current_user] = fake_moderator_user
    try:
        response = client.get("/moderator-only", headers={"Authorization": "Bearer faketoken"})
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
