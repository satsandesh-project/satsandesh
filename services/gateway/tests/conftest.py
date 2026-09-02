"""DB fixtures for tests/test_models.py.

Every import of SQLAlchemy or app.db.* is deferred inside the fixture
functions, not done at module level. This file is collected by pytest for
every test run in this directory, including tests that have nothing to do
with the database (test_health.py, test_auth.py, ...) — a module-level
import here would break their collection too, before app/db/ even exists.
Deferring means only a test that actually requests `engine`/`db_session`
pays for the missing dependency.

Assumes the target database's schema was already created by
`alembic upgrade head` — these fixtures do not call `Base.metadata.create_all`
or run migrations themselves. Point `TEST_DATABASE_URL` at a database you've
already migrated (see services/gateway/README.md).
"""

import os

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg2://postgres:devpass@localhost:5432/satsandesh_test",
)


@pytest.fixture(scope="session")
def engine():
    from sqlalchemy import create_engine

    eng = create_engine(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    """One SQLAlchemy Session per test, with the tables it touched wiped
    afterward. Deliberately not a wrap-in-a-transaction-and-roll-back
    pattern: several tests need to assert an IntegrityError and then keep
    issuing statements in the same test (e.g. two separate invalid inserts),
    which would otherwise require SAVEPOINT bookkeeping this doesn't need.
    Truncating between tests is simpler and just as isolated."""
    from sqlalchemy.orm import Session

    from app.db.base import Base

    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())


@pytest.fixture()
def client(db_session):
    """TestClient wired to the real app.main.app, with the get_db
    dependency overridden to yield this test's db_session (the same real
    Postgres session route-test setup code uses directly) instead of the
    app opening its own connection per request. Route handlers run
    synchronously in FastAPI's threadpool for a TestClient call, which
    blocks until the response comes back — so despite crossing an OS
    thread boundary, there's never truly concurrent access to db_session,
    only sequential access from test-setup code and then the request
    handler in turn. get_current_user is deliberately left un-overridden
    here; each test picks its own caller identity via the login_as
    fixture below, since which user "you" are is the whole point of the
    authorization tests this fixture backs."""
    from fastapi.testclient import TestClient

    from app.db.base import get_db
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def login_as():
    """Returns a function that makes the `client` fixture authenticate
    subsequent requests as a given app.db.models.User row, via FastAPI's
    dependency_overrides on get_current_user — the exact swap mechanism
    tests/test_auth.py already establishes for the same seam, just fed a
    real seeded DB user's identity instead of a hand-built stub, so route
    tests can exercise "two different authenticated callers" scenarios
    that the fixed production auth stub can't produce on its own."""
    from app.auth import get_current_user
    from app.main import app
    from app.models import User as WireUser

    def _login_as(db_user):
        wire_user = WireUser(
            id=str(db_user.id),
            name=db_user.name,
            preferred_language=db_user.preferred_language,
            role=db_user.role,
        )
        app.dependency_overrides[get_current_user] = lambda: wire_user
        return wire_user

    yield _login_as
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def ws_login_as(monkeypatch):
    """Returns a function that, given a real DB user row, returns a token
    string `/ws?token=...` will authenticate as that user.

    app/ws.py's `/ws` route calls `app.auth.user_from_token` directly, not
    via FastAPI's `Depends`/`dependency_overrides` the way `/me` does (the
    token travels as a query param, not a header, since browsers can't set
    custom headers on a WS handshake) — so the `login_as` fixture above,
    which relies on `dependency_overrides`, never reaches it. This patches
    the name as imported into `app.ws`'s own module namespace, not
    `app.auth`'s copy — same reasoning tests/test_health.py's
    `check_database_connection` patch documents: whichever module already
    imported the name is what actually resolves at call time.

    Also unlike the real `user_from_token` stub (which returns the same
    hardcoded user for any non-empty token), this supports more than one
    live identity at once — WS delivery tests need at least two distinct,
    simultaneously-connected users to prove fan-out reaches the right
    socket and not just any socket.
    """
    import app.ws as ws_module
    from app.models import User as WireUser

    tokens: dict[str, WireUser] = {}

    def _fake_user_from_token(token, db):
        from fastapi import HTTPException

        if token is None or token not in tokens:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return tokens[token]

    monkeypatch.setattr(ws_module, "user_from_token", _fake_user_from_token)

    def _login_as(db_user) -> str:
        token = f"token-{db_user.id}"
        tokens[token] = WireUser(
            id=str(db_user.id),
            name=db_user.name,
            preferred_language=db_user.preferred_language,
            role=db_user.role,
        )
        return token

    return _login_as


@pytest.fixture()
def _instant_fan_out(monkeypatch, engine):
    """Week 4 Phase 8 deferred every message.send's real delivery
    (message.new + push) by settings.UNDO_WINDOW_SECONDS, run later by
    app/undo.py's scheduler instead of inline in _handle_message_send. Any
    test exercising real delivery (tests/test_ws_delivery.py,
    tests/test_message_delivered.py) needs it to still actually happen
    during the test's real few-millisecond lifetime, without caring about
    the undo window itself (tests/test_undo.py owns that). Collapsing
    app/undo.py's real sleep to instant, and pointing its independent
    session at the test database, gets the real scheduled asyncio.Task (not
    a substitute) to run to completion almost immediately, preserving every
    assertion in either file unchanged — including the sender's-own-
    originating-socket exclusion, which only the real scheduled path
    (closing over the actual server-side WebSocket object) can reproduce; a
    test calling fan_out_message directly has no way to reconstruct that
    reference.

    Lives here, not in tests/test_ws_delivery.py where it was originally
    written: a second file needing the same fixture is exactly the case
    conftest.py exists for — pytest picks up a fixture defined here
    automatically, no import needed in either file, unlike cross-importing
    it directly from another test module (which ruff correctly flags as
    F811 redefinition at every test that requests it as a parameter).

    A real sessionmaker bound to the shared test `engine` — same shape as
    production's own app.db.base.SessionLocal — not `lambda: db_session`:
    fan_out_message calls `session.close()` when it's done, same as
    production does on its own independently-opened session. Handing it a
    test's *shared* db_session directly means that close() call detaches
    every object the test itself already loaded (alice, bob, circle, ...)
    right as `expire_on_commit`'s default (True, for a bare `Session(engine)`
    with none of production SessionLocal's overrides) tries to refresh them
    from a session that's no longer there — a real DetachedInstanceError
    tests/test_ws_delivery.py's tests hit until this was caught. A separate
    session on the same underlying database sidesteps it entirely:
    Postgres's read-committed default means it sees everything db_session
    already committed (the WS handler commits before the ack, before
    fan-out is even scheduled), and db_session's own already-loaded objects
    are never touched by it.

    Not autouse: some tests (e.g. ConnectionManager's own unit tests in
    tests/test_ws_delivery.py) build a bare ConnectionManager() and touch
    no DB at all — forcing a db_session dependency on them via an autouse
    fixture would give them a Postgres dependency they don't otherwise
    have.
    """
    from sqlalchemy.orm import sessionmaker

    import app.undo as undo_module
    import app.ws as ws_module

    test_session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(ws_module, "SessionLocal", test_session_local)

    async def instant_sleep(_delay):
        return None

    monkeypatch.setattr(undo_module, "asyncio_sleep", instant_sleep)
