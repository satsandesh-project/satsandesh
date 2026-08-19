"""Model-layer tests for app/db/models.py and app/id.py, run against a real
Postgres database migrated with `alembic upgrade head` — CHECK constraints,
a partial index, and native uuid ordering are exercised here, none of which
SQLite reproduces faithfully, so there's no lighter-weight substitute. See
tests/conftest.py for how the test database is wired up and why its
fixtures defer their imports.

Written before app/id.py, app/db/base.py, or app/db/models.py exist —
collecting this file is expected to fail (ModuleNotFoundError) until Step
2's implementation lands. That failure is the point: these tests fix the
contract the implementation has to satisfy, not the other way around.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Circle, Conversation, Membership, Message, User
from app.id import generate_uuid7


def test_uuid7_helper_is_time_ordered():
    # No DB involved — a pure function test of app/id.py.
    first = generate_uuid7()
    import time

    time.sleep(0.005)
    second = generate_uuid7()

    assert isinstance(first, uuid.UUID)
    assert isinstance(second, uuid.UUID)
    # Raw uuid.UUID comparison (compares the 128-bit int), not str(first) <
    # str(second) — a string compare would pass for the wrong reason and
    # wouldn't catch a version/variant-bit bug in the byte layout.
    assert second > first


def test_user_roundtrip(db_session):
    user = User(name="Lakshmi Devi", preferred_language="te")
    db_session.add(user)
    db_session.commit()

    fetched = db_session.get(User, user.id)
    assert fetched is not None
    assert fetched.id == user.id
    assert fetched.name == "Lakshmi Devi"
    assert fetched.preferred_language == "te"
    assert fetched.photo is None
    assert fetched.tts_on is True
    assert fetched.role == "elder"
    assert fetched.timezone is None
    assert fetched.created_at is not None


def test_circle_and_membership_roundtrip(db_session):
    owner = User(name="Moderator Raju", preferred_language="te")
    db_session.add(owner)
    db_session.flush()

    circle = Circle(name="Evening Satsang", created_by=owner.id)
    db_session.add(circle)
    db_session.flush()

    membership = Membership(circle_id=circle.id, user_id=owner.id, role="admin")
    db_session.add(membership)
    db_session.commit()

    fetched = db_session.get(Membership, (circle.id, owner.id))
    assert fetched is not None
    assert fetched.circle_id == circle.id
    assert fetched.user_id == owner.id
    assert fetched.role == "admin"
    # No separate `id` column — contracts/chat/circles.py::Membership has
    # no `id` field on the wire, so storage shouldn't invent one either.
    assert not hasattr(Membership, "id")


def test_conversation_canonical_ordering(db_session):
    alice = User(name="Alice", preferred_language="en")
    bob = User(name="Bob", preferred_language="en")
    db_session.add_all([alice, bob])
    db_session.flush()

    high, low = sorted([alice.id, bob.id], reverse=True)

    backwards = Conversation(user_a=high, user_b=low)
    db_session.add(backwards)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_message_target_exactly_one(db_session):
    author = User(name="Author", preferred_language="en")
    other = User(name="Other", preferred_language="en")
    db_session.add_all([author, other])
    db_session.flush()

    circle = Circle(name="Test Circle", created_by=author.id)
    db_session.add(circle)
    db_session.flush()

    # Both target_user_id and target_circle_id point at real rows, so an
    # IntegrityError here can only be the num_nonnulls CHECK, not a
    # dangling FK — target_type='user' also keeps the circle/conversation_id
    # CHECK out of the way, isolating the one constraint under test.
    both_set = Message(
        client_msg_id=uuid.uuid4(),
        author_id=author.id,
        target_type="user",
        target_user_id=other.id,
        target_circle_id=circle.id,
        conversation_id=generate_uuid7(),
        kind="text",
        text="hi",
    )
    db_session.add(both_set)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    neither_set = Message(
        client_msg_id=uuid.uuid4(),
        author_id=author.id,
        target_type="user",
        conversation_id=generate_uuid7(),
        kind="text",
        text="hi",
    )
    db_session.add(neither_set)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_message_kind_requires_matching_payload(db_session):
    author = User(name="Author", preferred_language="en")
    other = User(name="Other", preferred_language="en")
    db_session.add_all([author, other])
    db_session.flush()

    text_without_body = Message(
        client_msg_id=uuid.uuid4(),
        author_id=author.id,
        target_type="user",
        target_user_id=other.id,
        conversation_id=generate_uuid7(),
        kind="text",
        text=None,
    )
    db_session.add(text_without_body)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    voice_without_media = Message(
        client_msg_id=uuid.uuid4(),
        author_id=author.id,
        target_type="user",
        target_user_id=other.id,
        conversation_id=generate_uuid7(),
        kind="voice",
        original_media_ref=None,
    )
    db_session.add(voice_without_media)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_client_msg_id_idempotent_per_author(db_session):
    # This is the constraint Week 2 Phase 3's create_message will rely on
    # for offline-retry idempotency (contracts/chat/README.md design
    # decision #2) — proving it exists at the schema level now.
    author = User(name="Author", preferred_language="en")
    other = User(name="Other", preferred_language="en")
    db_session.add_all([author, other])
    db_session.flush()

    shared_client_msg_id = uuid.uuid4()

    first_send = Message(
        client_msg_id=shared_client_msg_id,
        author_id=author.id,
        target_type="user",
        target_user_id=other.id,
        conversation_id=generate_uuid7(),
        kind="text",
        text="first try",
    )
    db_session.add(first_send)
    db_session.commit()

    retry_after_dropped_ack = Message(
        client_msg_id=shared_client_msg_id,
        author_id=author.id,
        target_type="user",
        target_user_id=other.id,
        conversation_id=generate_uuid7(),
        kind="text",
        text="retried send",
    )
    db_session.add(retry_after_dropped_ack)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_soft_deleted_message_still_readable_by_id_but_excluded_by_partial_index(
    db_session,
):
    author = User(name="Author", preferred_language="en")
    other = User(name="Other", preferred_language="en")
    db_session.add_all([author, other])
    db_session.flush()

    conversation_id = generate_uuid7()
    message = Message(
        client_msg_id=uuid.uuid4(),
        author_id=author.id,
        target_type="user",
        target_user_id=other.id,
        conversation_id=conversation_id,
        kind="text",
        text="see you later",
    )
    db_session.add(message)
    db_session.commit()

    message.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    # The row isn't gone — a direct lookup by id still finds it.
    by_id = db_session.get(Message, message.id)
    assert by_id is not None
    assert by_id.deleted_at is not None

    # But the same shape as the sync index's query excludes it, exactly as
    # docs/SCHEMA_DRAFT.md's messages index #3 and design question #2
    # require of every list/sync query.
    sync_shape_results = (
        db_session.execute(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.deleted_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    assert sync_shape_results == []
