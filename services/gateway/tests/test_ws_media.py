"""WebSocket mirror of tests/test_message_media.py: `message.send` must not
accept a voice message whose media_ref doesn't resolve to a media object
the sender owns.

app/messages.py's POST /messages closed this gap in PR #73, but app/ws.py's
own `message.send` handler calls the same create function independently and
was left passing `media_ref.uri` straight through. Written before the WS
handler validates anything: the "must be rejected" tests are expected to
fail today (an ack where an error frame belongs); the "succeeds" test
should fail too, but only on the readback half (media_ref arrives null).
"""

import uuid

from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import create_media_object


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _upload_real_media(db_session, *, author_id, format="ogg_opus", duration_ms=4000):
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


def _voice_frame(*, client_msg_id, target_id, uri, fmt="ogg_opus", duration_ms=4000):
    return {
        "type": "message.send",
        "data": {
            "client_msg_id": client_msg_id,
            "target_type": "user",
            "target_id": target_id,
            "kind": "voice",
            "media_ref": {"uri": uri, "format": fmt, "duration_ms": duration_ms},
        },
    }


def test_ws_voice_send_with_a_real_owned_artifact_is_acked_and_linked(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    media = _upload_real_media(db_session, author_id=alice.id)
    media_id = media.id
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _voice_frame(
                client_msg_id=str(uuid.uuid4()), target_id=str(bob.id), uri=f"media:{media_id}"
            )
        )
        ack = alice_ws.receive_json()
        assert ack["type"] == "message.ack"

        new = bob_ws.receive_json()
        assert new["type"] == "message.new"
        media_ref = new["data"]["media_ref"]
        assert media_ref is not None, "recipient must see the media_ref, not null"
        assert media_ref["uri"] == f"media:{media_id}"
        assert media_ref["format"] == "ogg_opus"

    row = db_session.get(Message, uuid.UUID(ack["data"]["id"]))
    assert row.media_object_id == media_id
    assert row.media_format == "ogg_opus"


def test_ws_voice_send_pointing_at_nothing_is_rejected_with_an_error_frame(
    client, db_session, ws_login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    client_msg_id = str(uuid.uuid4())

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(
            _voice_frame(
                client_msg_id=client_msg_id, target_id=str(bob.id), uri=f"media:{uuid.uuid4()}"
            )
        )
        frame = alice_ws.receive_json()

    assert frame["type"] == "error", f"expected an error frame, got {frame['type']}"
    assert frame["data"]["code"] == "VALIDATION_FAILED"
    # Same as the circle-authorization errors: the client must be able to
    # tell WHICH optimistic "Sending..." bubble to mark failed.
    assert frame["data"]["detail"]["client_msg_id"] == client_msg_id
    assert db_session.query(Message).filter(Message.author_id == alice.id).count() == 0


def test_ws_not_found_and_wrong_owner_error_frames_are_identical(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    carol = _make_db_user(db_session, "Carol")
    bobs_media = _upload_real_media(db_session, author_id=bob.id)
    bobs_media_id = bobs_media.id
    alice_token = ws_login_as(alice)

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(
            _voice_frame(
                client_msg_id="00000000-0000-4000-8000-000000000001",
                target_id=str(carol.id),
                uri=f"media:{uuid.uuid4()}",
            )
        )
        nonexistent = alice_ws.receive_json()
        alice_ws.send_json(
            _voice_frame(
                client_msg_id="00000000-0000-4000-8000-000000000001",
                target_id=str(carol.id),
                uri=f"media:{bobs_media_id}",
            )
        )
        wrong_owner = alice_ws.receive_json()

    assert nonexistent["type"] == wrong_owner["type"] == "error"
    assert nonexistent == wrong_owner, "an id is guessable; ownership must not be observable"


def test_ws_voice_send_with_unrecognized_scheme_is_rejected(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(
            _voice_frame(
                client_msg_id=str(uuid.uuid4()),
                target_id=str(bob.id),
                uri="s3://some-bucket/some-key",
            )
        )
        frame = alice_ws.receive_json()

    assert frame["type"] == "error"
    assert frame["data"]["code"] == "VALIDATION_FAILED"
