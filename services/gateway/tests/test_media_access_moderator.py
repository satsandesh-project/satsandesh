"""A moderator must be able to hear what they are asked to review.

`ModerationQueueItem.original_media_ref` is "the voice note itself, so a
moderator can listen when the transcript is the doubtful part". Week 6's
download rule (author, or recipient of a delivered message) means a HELD
message's audio is reachable by its author only -- so the console would show
a play button that returns 404. This adds the narrow allowance:

  a moderator or admin (users.role) may fetch the audio of a message that is
  `held` or `blocked` (and not deleted) -- nothing else.

Deliberately NOT granted: a `pending` message (still inside the sender's undo
window -- not the moderators' business yet), a `cancelled` one, a deleted
one, or a `sent`/`delivered` message the moderator is not a recipient of (the
queue is for what needs review, not a license to listen to everything).
Everyone refused still gets the same 404 as a missing id.

Written before user_can_fetch_media knows about roles.
"""

import uuid

import pytest

from app.db.models import User as DbUser
from app.db.repository import (
    create_media_object,
    create_message,
    set_message_status,
)
from app.media_storage import get_media_storage


def _user(db_session, name, role="elder"):
    user = DbUser(name=name, preferred_language="en", role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _held_voice(db_session, *, author, target, status):
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author.id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=1000,
    )
    db_session.commit()
    media_id = media.id
    get_media_storage().put(str(media_id), b"the-note")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="voice",
        original_media_ref=f"media:{media_id}",
        media_duration_ms=1000,
        media_object_id=media_id,
        media_format="wav_pcm16",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    message_id = message.id
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return media_id, message_id


@pytest.mark.parametrize("role", ["moderator", "admin"])
@pytest.mark.parametrize("status", ["held", "blocked"])
def test_a_moderator_or_admin_can_fetch_the_audio_of_a_held_or_blocked_message(
    client, db_session, login_as, role, status
):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    mod = _user(db_session, "Mod", role=role)
    media_id, _ = _held_voice(db_session, author=alice, target=bob, status=status)
    login_as(mod)

    resp = client.get(f"/media/{media_id}")

    assert resp.status_code == 200
    assert resp.content == b"the-note"


def test_an_ordinary_user_still_cannot_fetch_a_held_message_they_are_not_in(
    client, db_session, login_as
):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    carol = _user(db_session, "Carol")  # an elder, not a moderator
    media_id, _ = _held_voice(db_session, author=alice, target=bob, status="held")
    login_as(carol)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_the_recipient_of_a_held_message_still_cannot_fetch_it(client, db_session, login_as):
    # Unchanged from Week 6: the recipient was never meant to hear it yet.
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    media_id, _ = _held_voice(db_session, author=alice, target=bob, status="held")
    login_as(bob)

    assert client.get(f"/media/{media_id}").status_code == 404


@pytest.mark.parametrize("status", ["pending", "cancelled", "failed"])
def test_a_moderator_cannot_fetch_audio_of_a_message_that_is_not_under_review(
    client, db_session, login_as, status
):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    mod = _user(db_session, "Mod", role="moderator")
    media_id, _ = _held_voice(db_session, author=alice, target=bob, status=status)
    login_as(mod)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_a_moderator_gets_no_access_to_delivered_messages_they_are_not_a_recipient_of(
    client, db_session, login_as
):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    mod = _user(db_session, "Mod", role="moderator")
    media_id, _ = _held_voice(db_session, author=alice, target=bob, status="sent")
    login_as(mod)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_a_deleted_held_message_grants_a_moderator_nothing(client, db_session, login_as):
    from datetime import UTC, datetime

    from app.db.models import Message

    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    mod = _user(db_session, "Mod", role="moderator")
    media_id, message_id = _held_voice(db_session, author=alice, target=bob, status="held")
    db_session.get(Message, message_id).deleted_at = datetime.now(UTC)
    db_session.commit()
    login_as(mod)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_a_refused_moderator_gets_the_same_404_as_a_missing_id(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    mod = _user(db_session, "Mod", role="moderator")
    media_id, _ = _held_voice(db_session, author=alice, target=bob, status="pending")
    login_as(mod)

    refused = client.get(f"/media/{media_id}")
    missing = client.get(f"/media/{uuid.uuid4()}")

    assert refused.status_code == missing.status_code == 404
    assert refused.json() == missing.json()
