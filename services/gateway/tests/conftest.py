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
