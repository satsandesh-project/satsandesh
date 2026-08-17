"""Tests for the gateway's HTTP endpoints.

/db-check talks to Postgres, so the tests below fake the database connection
rather than requiring a live one — that keeps `pytest` runnable without
`docker compose up`. The real end-to-end check is the curl against
http://localhost/db-check described in README.md.
"""

import psycopg
from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- fake DB objects, standing in for psycopg -----------------------------
# psycopg's connection and cursor are both context managers, so the fakes
# need __enter__/__exit__ to be drop-in replacements.


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, *args, **kwargs):
        pass  # nothing to do — the row is decided up front

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def cursor(self):
        return _FakeCursor(self._row)


# --- /db-check ------------------------------------------------------------


def test_db_check_ok(monkeypatch):
    """Seeded row present -> 200, and the note is echoed back."""
    monkeypatch.setattr(
        main.psycopg, "connect", lambda *a, **kw: _FakeConnection(("db init ran",))
    )

    response = client.get("/db-check")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_check": "db init ran"}


def test_db_check_table_empty(monkeypatch):
    """Table exists but is empty -> init did not seed it, so report 500."""
    monkeypatch.setattr(
        main.psycopg, "connect", lambda *a, **kw: _FakeConnection(None)
    )

    response = client.get("/db-check")

    assert response.status_code == 500
    assert "init did not run" in response.json()["detail"]


def test_db_check_db_unreachable(monkeypatch):
    """Postgres down -> 503 (service unavailable), not an unhandled 500."""

    def _refuse_connection(*args, **kwargs):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(main.psycopg, "connect", _refuse_connection)

    response = client.get("/db-check")

    assert response.status_code == 503
    assert "database unreachable" in response.json()["detail"]
