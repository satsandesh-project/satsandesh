import socket

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


@pytest.fixture
def client():
    # Entering as a context manager makes TestClient stand up its anyio
    # portal (and the OS socketpair that requires) once, up front — before
    # any test gets a chance to monkeypatch socket internals below. Outside
    # a `with` block, TestClient builds a *fresh* portal (and thus a fresh
    # socketpair) on every single .get() call, which would make the
    # dependency-free test below fail on the test harness's own plumbing
    # instead of on the handler.
    with TestClient(app) as test_client:
        yield test_client


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_dependency_free(client, monkeypatch):
    # /health is the liveness probe Docker Compose, Caddy, and Uptime-Kuma all
    # poll directly. It must never be able to touch the database, the network,
    # or a model call — if it did, a blip in one of those would make an
    # orchestrator kill and restart an otherwise-healthy gateway process. We
    # can't prove a negative "makes no I/O call" statically, so instead we
    # sabotage the socket primitive any such call would have to go through
    # and prove the response still succeeds untouched. The `client` fixture
    # already has its portal open by this point, so this only catches I/O
    # attempted by the /health handler itself, not TestClient's plumbing.
    def _boom(*args, **kwargs):
        raise AssertionError("/health must not perform network or socket I/O")

    monkeypatch.setattr(socket.socket, "connect", _boom)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_shape(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "checks" in body
    assert isinstance(body["checks"], dict)


def test_ready_returns_200_when_database_reachable(client):
    # Requires the real dev Postgres (services/gateway/.env's
    # DATABASE_URL) to actually be running locally — same standing
    # requirement test_repository.py and test_models.py already have for
    # the separate satsandesh_test database; see README.md's "Database"
    # section. check_database_connection() (app/db/base.py) is called
    # directly against the module-level engine, not through get_db, so
    # this test needs no dependency override.
    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["postgres"] == "ok"


def test_ready_returns_503_when_database_unreachable(client, monkeypatch):
    # check_database_connection is imported into app/main.py's own module
    # namespace (`from app.db.base import check_database_connection`), so
    # — same reasoning as README.md's "What is stubbed" section on
    # user_from_token — patching it on app.main is what the route
    # function actually resolves at call time; patching app.db.base's
    # copy instead would leave app.main's already-imported reference
    # untouched.
    monkeypatch.setattr(main_module, "check_database_connection", lambda: False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] != "ok"
    assert body["checks"]["postgres"] != "ok"
