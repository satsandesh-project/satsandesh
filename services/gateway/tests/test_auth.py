import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import auth as auth_module
from app import ws as ws_module
from app.auth import get_current_user, require_role
from app.main import app
from app.models import User


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def role_test_client():
    # require_role has no route in app/main.py to exercise it — that route
    # was a demo, not a real feature, and got removed (Week 1 review) so no
    # fake endpoint ships in the route table. This throwaway app proves the
    # same behavior (require_role rejects the wrong role; get_current_user
    # stays swappable via dependency_overrides) without touching app.main's
    # actual app object or its route table.
    test_app = FastAPI()

    @test_app.get("/protected")
    def protected(user: User = Depends(require_role("moderator"))) -> User:
        return user

    with TestClient(test_app) as test_client:
        yield test_app, test_client


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


def test_protected_route_rejects_wrong_role(role_test_client):
    # require_role("moderator") on /protected. The stub user in get_current_user
    # is hardcoded to role="elder", so an authenticated-but-wrong-role caller must
    # get 403 (authorization failure), not 401 (authentication failure) — those are
    # different failure modes and callers need to be able to tell them apart.
    _, client = role_test_client
    response = client.get("/protected", headers={"Authorization": "Bearer faketoken"})
    assert response.status_code == 403


def test_dependency_override_swaps_current_user(role_test_client):
    # Demonstrates that get_current_user is a swappable seam: overriding it here
    # (via app.dependency_overrides, FastAPI's supported DI-swap mechanism) is the
    # same mechanism next week's real-JWT implementation will use in production —
    # only the function body changes, not each route.
    test_app, client = role_test_client

    def fake_moderator_user():
        return User(id="override-1", name="Override Mod", preferred_language="en", role="moderator")

    test_app.dependency_overrides[get_current_user] = fake_moderator_user
    try:
        response = client.get("/protected", headers={"Authorization": "Bearer faketoken"})
        assert response.status_code == 200
    finally:
        test_app.dependency_overrides.clear()


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
