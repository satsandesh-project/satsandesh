"""A brand-new user's first requests must not wait on each other.

Seen on staging on 2026-10-12: the gateway froze for minutes and its health check failed. A new
browser sends several requests at once with the same brand-new token. Each one provisions the
`users` row (`get_or_create_user`) in `get_current_user`, which is `async def` and runs its
blocking database calls on the event loop. The first request's INSERT stayed uncommitted until
its session was closed, and closing it needs the event loop. The second request's INSERT of the
same primary key then blocks the loop, waiting for a transaction that can only finish if the loop
runs: a deadlock that only Postgres's statement timeout ends (15 s each, so minutes for a dozen
requests), with every pooled connection idle in a transaction meanwhile.

These tests do not need the event loop to show it. They use two sessions the way two such
requests do: the first provisions and is NOT closed (its route is "still running"); the second
arrives with the same token.
"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth import user_from_token
from tests.conftest import TEST_DATABASE_URL

# The second request gets a short fuse: if it has to wait for the first one's transaction, the
# test fails in a couple of seconds instead of hanging until a human notices.
SHORT_FUSE_MS = 1500


@pytest.fixture()
def token(engine):
    value = str(uuid.uuid4())
    yield value
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": value})


def _row_count(engine, token: str) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(*) FROM users WHERE id = :id"), {"id": token}
        ).scalar_one()


def test_a_new_users_row_is_committed_as_soon_as_it_is_provisioned(engine, token) -> None:
    first = Session(engine)
    try:
        user = user_from_token(token, first)
        assert user.id == token
        # Seen from another connection, while the first request's session is still open.
        assert _row_count(engine, token) == 1
    finally:
        first.close()


def test_a_second_first_request_for_the_same_new_token_does_not_wait_for_the_first(
    engine, token
) -> None:
    impatient = create_engine(
        TEST_DATABASE_URL, connect_args={"options": f"-c statement_timeout={SHORT_FUSE_MS}"}
    )
    first = Session(engine)
    second = Session(impatient)
    try:
        user_from_token(token, first)  # request 1 provisions; its session stays open
        user = user_from_token(token, second)  # request 2, the same brand-new token, at once
        assert user.id == token
        assert _row_count(engine, token) == 1
    finally:
        second.close()
        first.close()
        impatient.dispose()


def test_a_token_that_already_has_a_row_is_not_inserted_again(engine, token) -> None:
    with Session(engine) as setup:
        user_from_token(token, setup)
    again = Session(engine)
    try:
        user_from_token(token, again)
        assert _row_count(engine, token) == 1
    finally:
        again.close()
