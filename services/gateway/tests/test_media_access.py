"""GET /media/{id} must not hand any logged-in user any voice note.

Before this, the route only required *a* valid token: a media id is a
UUID7 (time-ordered, partly guessable) and, with this app's stub auth,
anyone holding any UUID counts as logged in. So anyone could fetch anyone's
voice note by id. Access is now: the author always; otherwise only a
recipient of a message that carries this media AND has actually been
delivered (`sent`/`delivered`) -- not one still inside its undo window,
undone, held or blocked, since those recipients were never meant to hear it.

A caller without access gets the same 404 as an id that doesn't exist: a
403 would confirm the id is real.

Written before the route checks anything.
"""

import uuid
from datetime import UTC, datetime

from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import (
    add_member,
    create_circle,
    create_media_object,
    create_message,
    set_message_status,
)
from app.media_storage import get_media_storage


def _user(db_session, name):
    user = DbUser(name=name, preferred_language="en", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _media(db_session, author_id):
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author_id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=1000,
    )
    db_session.commit()
    return media


def _store_bytes(media_id, data=b"voice-bytes"):
    get_media_storage().put(str(media_id), data)


def _dm_voice(db_session, *, author, target, media, status="sent"):
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="voice",
        original_media_ref=f"media:{media.id}",
        media_duration_ms=1000,
        media_object_id=media.id,
        media_format="wav_pcm16",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    message_id = message.id
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return message_id


def test_the_author_can_fetch_their_own_media(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    login_as(alice)

    assert client.get(f"/media/{media_id}").status_code == 200


def test_an_unrelated_user_gets_the_same_404_as_a_missing_id(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    mallory = _user(db_session, "Mallory")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    login_as(mallory)

    stranger = client.get(f"/media/{media_id}")
    missing = client.get(f"/media/{uuid.uuid4()}")

    assert stranger.status_code == 404
    assert stranger.status_code == missing.status_code
    assert stranger.json() == missing.json(), "must not reveal that the id is real"


def test_the_recipient_of_a_delivered_voice_message_can_fetch_it(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id, b"alice-says-hi")
    _dm_voice(db_session, author=alice, target=bob, media=media, status="sent")
    login_as(bob)

    resp = client.get(f"/media/{media_id}")

    assert resp.status_code == 200
    assert resp.content == b"alice-says-hi"


def test_the_recipient_cannot_fetch_before_the_message_is_delivered(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    _dm_voice(db_session, author=alice, target=bob, media=media, status="pending")
    login_as(bob)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_the_recipient_cannot_fetch_audio_of_an_undone_message(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    _dm_voice(db_session, author=alice, target=bob, media=media, status="cancelled")
    login_as(bob)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_the_recipient_cannot_fetch_audio_of_a_held_message(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    _dm_voice(db_session, author=alice, target=bob, media=media, status="held")
    login_as(bob)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_a_third_party_cannot_fetch_someone_elses_dm_audio(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    carol = _user(db_session, "Carol")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    _dm_voice(db_session, author=alice, target=bob, media=media, status="sent")
    login_as(carol)

    assert client.get(f"/media/{media_id}").status_code == 404


def test_circle_members_can_fetch_a_delivered_circle_voice_note_but_outsiders_cannot(
    client, db_session, login_as
):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    outsider = _user(db_session, "Outsider")
    circle = create_circle(db_session, name="Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="circle",
        target_circle_id=circle.id,
        kind="voice",
        original_media_ref=f"media:{media_id}",
        media_duration_ms=1000,
        media_object_id=media_id,
        media_format="wav_pcm16",
        client_msg_id=uuid.uuid4(),
    )
    message_id = message.id
    db_session.commit()
    set_message_status(db_session, message_id, new_status="sent", expected="pending")
    db_session.commit()

    login_as(bob)
    assert client.get(f"/media/{media_id}").status_code == 200
    login_as(outsider)
    assert client.get(f"/media/{media_id}").status_code == 404


def test_a_soft_deleted_message_no_longer_grants_access(client, db_session, login_as):
    alice = _user(db_session, "Alice")
    bob = _user(db_session, "Bob")
    media = _media(db_session, alice.id)
    media_id = media.id
    _store_bytes(media_id)
    message_id = _dm_voice(db_session, author=alice, target=bob, media=media, status="sent")
    db_session.execute(
        Message.__table__.update()
        .where(Message.id == message_id)
        .values(deleted_at=datetime.now(UTC))
    )
    db_session.commit()
    login_as(bob)

    assert client.get(f"/media/{media_id}").status_code == 404
