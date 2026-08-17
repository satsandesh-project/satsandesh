import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app import ws as ws_module
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


def test_user_from_token_is_the_same_object_for_http_and_ws():
    # README.md's "What is stubbed and who replaces it" promises one swap
    # point: editing user_from_token's body covers both auth paths. But the
    # two call sites reach it through different mechanisms — get_current_user
    # (here in app/auth.py) resolves it as a module global at call time, while
    # app/ws.py did `from app.auth import user_from_token` and holds its own
    # copy of the object from import time. Those only agree because both
    # currently point at the exact same function. If a future edit rebinds
    # user_from_token (module-level reassignment, mock.patch left in place,
    # anything other than editing the body in place) after both modules are
    # already imported, get_current_user picks up the change immediately and
    # app/ws.py's copy silently doesn't — this assertion is what catches that,
    # since a black-box comparison of /me vs. /ws output can't: both return
    # identical hardcoded stub data today regardless of whether they share an
    # implementation or just happen to agree.
    assert ws_module.user_from_token is auth_module.user_from_token
