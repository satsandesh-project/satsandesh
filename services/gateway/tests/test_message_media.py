"""Tests for wiring a `kind: "voice"` message's `media_ref` to a real,
owned `media_objects` row (app/messages.py, app/db/repository.py,
app/db/models.py).

Written before the validation/FK exist -- posting a voice message with a
dangling or unowned media_ref is expected to succeed today (a real,
confirmed gap; see the Step 0 write-up), so the "must be rejected" tests
below are the ones expected to fail for the wrong reason initially: a 200
where a 4xx belongs. The "succeeds" and "still works" tests should
already pass, since POST /messages itself already exists -- what's
missing is the read-side media_ref (always None today) and the
write-side validation (always accepted today).
"""

import uuid

from app.db.models import MediaObject, Message
from app.db.models import User as DbUser
from app.db.repository import create_media_object, create_message


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _upload_real_media(db_session, *, author_id, format="wav_pcm16", duration_ms=3000):
    media, _created = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author_id,
        format=format,
        sha256_hex=uuid.uuid4().hex,
        size_bytes=4096,
        duration_ms=duration_ms,
    )
    db_session.commit()
    return media


def test_voice_message_with_a_real_owned_artifact_succeeds(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    media = _upload_real_media(db_session, author_id=alice.id)
    login_as(alice)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "voice",
            "media_ref": {
                "uri": f"media:{media.id}",
                "format": "wav_pcm16",
                "duration_ms": 3000,
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["id"], str) and body["id"]


def test_voice_message_pointing_at_nothing_is_rejected_with_a_readable_4xx(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "voice",
            "media_ref": {
                "uri": f"media:{uuid.uuid4()}",  # syntactically valid, never uploaded
                "format": "wav_pcm16",
                "duration_ms": 3000,
            },
        },
    )

    assert response.status_code < 500, (
        f"must never be a 500 -- got {response.status_code}: {response.text}"
    )
    assert 400 <= response.status_code < 500
    assert "detail" in response.json()

    # And it must not have silently created the message either -- a 4xx
    # response with a row left behind would be its own kind of lie.
    assert db_session.query(Message).filter(Message.author_id == alice.id).count() == 0


def test_voice_message_pointing_at_another_authors_artifact_is_rejected(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    carol = _make_db_user(db_session, "Carol")
    bobs_media = _upload_real_media(db_session, author_id=bob.id)
    login_as(alice)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(carol.id),
            "kind": "voice",
            "media_ref": {
                "uri": f"media:{bobs_media.id}",
                "format": "wav_pcm16",
                "duration_ms": 3000,
            },
        },
    )

    assert 400 <= response.status_code < 500
    assert "detail" in response.json()


def test_not_found_and_wrong_owner_return_identical_responses(client, db_session, login_as):
    # Security property, not just a UX nicety: differentiating "doesn't
    # exist" from "exists but isn't yours" would let a client enumerate
    # which media ids are real by watching which error comes back --
    # exactly what "an id is guessable; ownership is not" (the task's own
    # framing) is warning against. Same status, same detail, for both.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    dave = _make_db_user(db_session, "Dave")
    bobs_media = _upload_real_media(db_session, author_id=bob.id)

    login_as(alice)
    nonexistent = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(dave.id),
            "kind": "voice",
            "media_ref": {"uri": f"media:{uuid.uuid4()}", "format": "wav_pcm16"},
        },
    )
    wrong_owner = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(dave.id),
            "kind": "voice",
            "media_ref": {"uri": f"media:{bobs_media.id}", "format": "wav_pcm16"},
        },
    )

    assert nonexistent.status_code == wrong_owner.status_code
    assert nonexistent.json() == wrong_owner.json()


def test_voice_message_with_unrecognized_uri_scheme_is_rejected(client, db_session, login_as):
    # This deployment's only real backend is the "media:" scheme
    # (app/media.py's POST/GET /media) -- nothing else can be verified to
    # exist or be owned by anyone, so nothing else is accepted.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "voice",
            "media_ref": {"uri": "s3://some-bucket/some-key", "format": "wav_pcm16"},
        },
    )

    assert 400 <= response.status_code < 500


def test_text_message_with_no_media_ref_still_works_exactly_as_before(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "text",
            "text": "hello",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["id"], str) and body["id"]


def test_message_out_media_ref_comes_back_populated_and_correct_for_a_voice_message(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    media = _upload_real_media(db_session, author_id=alice.id, format="ogg_opus", duration_ms=5000)
    login_as(alice)

    post = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "voice",
            "media_ref": {
                "uri": f"media:{media.id}",
                "format": "ogg_opus",
                "duration_ms": 5000,
            },
        },
    )
    assert post.status_code == 200

    login_as(bob)
    fetched = client.get("/messages", params={"target_type": "user", "target_id": str(alice.id)})
    assert fetched.status_code == 200
    messages = fetched.json()["messages"]
    assert len(messages) == 1
    media_ref = messages[0]["media_ref"]
    assert media_ref is not None, "media_ref must not be null for a real voice message"
    assert media_ref["uri"] == f"media:{media.id}"
    assert media_ref["format"] == "ogg_opus"
    assert media_ref["duration_ms"] == 5000


def test_text_message_media_ref_is_null_on_readback(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "text",
            "text": "hello",
        },
    )

    login_as(bob)
    fetched = client.get("/messages", params={"target_type": "user", "target_id": str(alice.id)})
    assert fetched.json()["messages"][0]["media_ref"] is None


def test_a_message_whose_media_was_since_swept_reads_back_with_null_media_ref(
    db_session,
):
    # Not through the route -- this exercises the FK's ON DELETE SET NULL
    # behavior directly (app/db/models.py::Message.media_object_id), the
    # same mechanism app/retention.py's sweeper relies on to make a swept
    # message's media_ref disappear on the next read rather than pointing
    # at audio that 404s when actually fetched.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    media = _upload_real_media(db_session, author_id=alice.id)
    # Captured as plain values before the delete below -- db_session
    # expires ORM instances on commit by default, and media's own row
    # genuinely won't exist anymore after that delete; reading media.id
    # afterward would trigger a lazy reload against a row that's really
    # gone (ObjectDeletedError), not the comparison this test wants.
    media_id = media.id
    media_format = media.format

    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="voice",
        original_media_ref=f"media:{media_id}",
        media_duration_ms=3000,
        media_object_id=media_id,
        media_format=media_format,
        client_msg_id=uuid.uuid4(),
    )
    message_id = message.id
    db_session.commit()
    assert message.media_object_id == media_id

    db_session.execute(MediaObject.__table__.delete().where(MediaObject.id == media_id))
    db_session.commit()
    db_session.expire_all()

    refreshed = db_session.get(Message, message_id)
    assert refreshed.media_object_id is None, (
        "ON DELETE SET NULL should have cleared this automatically"
    )
    assert refreshed.original_media_ref == f"media:{media_id}", (
        "the historical text reference must survive even though the row it pointed at is gone"
    )
