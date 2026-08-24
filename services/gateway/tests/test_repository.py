"""Repository-layer tests for app/db/repository.py — plain functions between
routes and the database, run against a real Postgres database migrated with
`alembic upgrade head` (same fixtures as tests/test_models.py; see
tests/conftest.py).

Written before app/db/repository.py exists — collecting this file is
expected to fail (ModuleNotFoundError) until Step 2's implementation lands.
That failure is the point: these tests fix the contract the implementation
has to satisfy, not the other way around.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Circle, Conversation, Membership, Message, User
from app.db.repository import (
    _get_or_create_conversation,
    add_member,
    create_circle,
    create_message,
    find_conversation_id,
    get_messages_since,
    is_circle_member,
    list_circles_for_user,
)


def _make_user(db_session, name="User", preferred_language="en"):
    user = User(name=name, preferred_language=preferred_language)
    db_session.add(user)
    db_session.flush()
    return user


def test_create_message_persists_and_returns_row(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="hello",
        client_msg_id=uuid.uuid4(),
    )

    assert message.id is not None
    assert message.created_at is not None
    assert message.status == "pending"


def test_create_message_idempotent_on_client_msg_id(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")
    shared_client_msg_id = uuid.uuid4()

    first = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="first try",
        client_msg_id=shared_client_msg_id,
    )
    second = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="retried send with different text",
        client_msg_id=shared_client_msg_id,
    )

    assert second.id == first.id

    rows = (
        db_session.execute(
            select(Message).where(
                Message.author_id == alice.id,
                Message.client_msg_id == shared_client_msg_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_create_message_reraises_unrelated_integrity_error(db_session):
    # An invalid target combination (both target_user_id and
    # target_circle_id set) violates ck_messages_target_exactly_one, not
    # uq_messages_author_client_msg_id — create_message's except clause
    # must not mistake this for a client_msg_id retry and swallow it into
    # a confusing "duplicate" return; it has to propagate as-is.
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")
    circle = Circle(name="Test Circle", created_by=alice.id)
    db_session.add(circle)
    db_session.flush()

    with pytest.raises(IntegrityError):
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            target_circle_id=circle.id,
            kind="text",
            text="hi",
            client_msg_id=uuid.uuid4(),
        )

    # The SAVEPOINT rollback inside create_message already undid just the
    # failed insert — the outer transaction is still healthy, so db_session
    # stays usable without an explicit rollback here.
    rows = (
        db_session.execute(
            select(Message).where(
                Message.author_id == alice.id, Message.target_type == "user"
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


def test_create_message_same_client_msg_id_different_author_creates_separate_rows(
    db_session,
):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")
    carol = _make_user(db_session, "Carol")
    shared_client_msg_id = uuid.uuid4()

    from_alice = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=carol.id,
        kind="text",
        text="hi from alice",
        client_msg_id=shared_client_msg_id,
    )
    from_bob = create_message(
        db_session,
        author_id=bob.id,
        target_type="user",
        target_user_id=carol.id,
        kind="text",
        text="hi from bob",
        client_msg_id=shared_client_msg_id,
    )

    assert from_alice.id != from_bob.id


def test_create_message_dm_resolves_conversation_id_consistently(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    alice_to_bob = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="hi Bob",
        client_msg_id=uuid.uuid4(),
    )
    bob_to_alice = create_message(
        db_session,
        author_id=bob.id,
        target_type="user",
        target_user_id=alice.id,
        kind="text",
        text="hi Alice",
        client_msg_id=uuid.uuid4(),
    )

    assert alice_to_bob.conversation_id == bob_to_alice.conversation_id


def test_create_message_dm_reuses_existing_conversation_row(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="first",
        client_msg_id=uuid.uuid4(),
    )
    create_message(
        db_session,
        author_id=bob.id,
        target_type="user",
        target_user_id=alice.id,
        kind="text",
        text="second",
        client_msg_id=uuid.uuid4(),
    )

    low, high = sorted([alice.id, bob.id])
    rows = (
        db_session.execute(
            select(Conversation).where(
                Conversation.user_a == low, Conversation.user_b == high
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_get_or_create_conversation_recovers_from_concurrent_insert_race(db_session, engine):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")
    # Committed so the second, independent session/connection below can see
    # these users at all — an uncommitted row is invisible across sessions.
    db_session.commit()

    low, high = sorted([alice.id, bob.id])

    # A second, wholly separate Session/connection standing in for a
    # concurrent request handling the other side of this same DM.
    winner_session = Session(engine)
    winner_id = {}
    original_add = db_session.add

    def add_after_concurrent_winner_commits(instance, *args, **kwargs):
        # session.add() is the first thing _get_or_create_conversation does
        # once it has decided to take the insert path — reached only after
        # its own "does a row already exist" SELECT already returned
        # nothing, so db_session is genuinely on the insert path, not being
        # tricked into it. (Patching flush() instead of add() was tried
        # first and doesn't work: SQLAlchemy's autoflush fires on the
        # initial SELECT itself, before anything is pending, so it would
        # trigger the winner's commit too early and the SELECT would just
        # find the winner's row directly — never touching the insert path
        # or the except branch at all.) Committing the same (user_a,
        # user_b) pair from a wholly separate session right here, between
        # that SELECT and db_session's own INSERT reaching Postgres, is the
        # actual race: by the time db_session flushes its own insert below,
        # a conflicting row already exists and committed out from under it.
        db_session.add = original_add
        winner_row = Conversation(user_a=low, user_b=high)
        winner_session.add(winner_row)
        winner_session.commit()
        winner_id["value"] = winner_row.id
        return original_add(instance, *args, **kwargs)

    db_session.add = add_after_concurrent_winner_commits
    try:
        result = _get_or_create_conversation(db_session, alice.id, bob.id)
    finally:
        winner_session.close()

    # _get_or_create_conversation must have hit the IntegrityError, rolled
    # back only its own SAVEPOINT (not the whole transaction — see the
    # comment in app/db/repository.py on why a bare rollback() would be
    # wrong here), and re-selected: it returns the winner's row, not its
    # own, and doesn't raise.
    assert result.id == winner_id["value"]

    rows = (
        db_session.execute(
            select(Conversation).where(Conversation.user_a == low, Conversation.user_b == high)
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_create_message_circle_conversation_id_equals_target_circle_id(db_session):
    alice = _make_user(db_session, "Alice")
    circle = Circle(name="Evening Satsang", created_by=alice.id)
    db_session.add(circle)
    db_session.flush()

    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="circle",
        target_circle_id=circle.id,
        kind="text",
        text="hello circle",
        client_msg_id=uuid.uuid4(),
    )

    assert message.conversation_id == circle.id


def test_get_messages_since_returns_in_id_order_respecting_cursor(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(5)
    ]
    conversation_id = messages[0].conversation_id
    # Sort by id, not creation-loop order: UUIDv7 only guarantees ordering
    # across distinct milliseconds (docs/SCHEMA_DRAFT.md's "id as native
    # uuid" section) — a tight loop can generate several ids inside the
    # same millisecond, where tie-breaking is random-bit, not loop order.
    ordered = sorted(messages, key=lambda m: m.id)

    since_id = ordered[1].id
    result = get_messages_since(db_session, conversation_id=conversation_id, since_id=since_id)

    assert [m.id for m in result] == [m.id for m in ordered[2:]]
    assert result == sorted(result, key=lambda m: m.id)


def test_get_messages_since_excludes_soft_deleted(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(3)
    ]
    conversation_id = messages[0].conversation_id
    # Sort by id, not creation-loop order — see the identical comment in
    # test_get_messages_since_returns_in_id_order_respecting_cursor above.
    ordered = sorted(messages, key=lambda m: m.id)

    middle = ordered[1]
    middle.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    result = get_messages_since(db_session, conversation_id=conversation_id)

    assert [m.id for m in result] == [ordered[0].id, ordered[2].id]


def test_create_circle_and_add_member(db_session):
    alice = _make_user(db_session, "Alice")

    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    membership = add_member(db_session, circle_id=circle.id, user_id=alice.id)

    fetched = db_session.get(Membership, (circle.id, alice.id))
    assert fetched is not None
    assert fetched.role == "member"
    assert membership.circle_id == circle.id
    assert membership.user_id == alice.id


def test_find_conversation_id_returns_none_when_no_messages_sent(db_session):
    # Route-layer motivation: GET /messages on a DM that's never been
    # messaged must return an empty history without creating a
    # conversations row as a side effect of a read — that's what makes
    # find_conversation_id a separate, read-only function from the
    # private, insert-capable _get_or_create_conversation above.
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    assert find_conversation_id(db_session, alice.id, bob.id) is None

    rows = db_session.execute(select(Conversation)).scalars().all()
    assert rows == []


def test_find_conversation_id_returns_existing_conversation_regardless_of_argument_order(
    db_session,
):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")
    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="hi",
        client_msg_id=uuid.uuid4(),
    )

    assert find_conversation_id(db_session, alice.id, bob.id) == message.conversation_id
    assert find_conversation_id(db_session, bob.id, alice.id) == message.conversation_id


def test_is_circle_member_true_for_a_member(db_session):
    alice = _make_user(db_session, "Alice")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id)

    assert is_circle_member(db_session, circle_id=circle.id, user_id=alice.id) is True


def test_is_circle_member_false_for_a_non_member(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id)

    assert is_circle_member(db_session, circle_id=circle.id, user_id=bob.id) is False


def test_list_circles_for_user(db_session):
    alice = _make_user(db_session, "Alice")
    bob = _make_user(db_session, "Bob")

    circle_1 = create_circle(db_session, name="Circle One", created_by=alice.id)
    circle_2 = create_circle(db_session, name="Circle Two", created_by=alice.id)
    circle_3 = create_circle(db_session, name="Circle Three (not Alice's)", created_by=bob.id)

    add_member(db_session, circle_id=circle_1.id, user_id=alice.id)
    add_member(db_session, circle_id=circle_2.id, user_id=alice.id)
    add_member(db_session, circle_id=circle_3.id, user_id=bob.id)

    result = list_circles_for_user(db_session, user_id=alice.id)

    assert {c.id for c in result} == {circle_1.id, circle_2.id}
