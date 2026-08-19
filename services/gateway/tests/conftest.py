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
