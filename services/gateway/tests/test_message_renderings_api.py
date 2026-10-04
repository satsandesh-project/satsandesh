"""Week 7 Phase 4: what a client actually receives.

Stored pipeline output (test_renderings_store.py) is only useful if it
reaches the receiver screen: in `GET /messages`, in a WebSocket `sync.batch`,
and in the `message.new` frame fan-out pushes -- one mapping, three places
(app/messages.py::message_to_out is shared by all of them). And a rendering's
audio is only useful if the receiver may fetch it: Week 6's download rule
knew a media object only through `messages.media_object_id`, so a rendering's
audio would have been refused to the very person it was rendered for.

Visibility follows the contract: a message that is not out yet (pending, held,
blocked) has no renderings and no transcript on the wire, even though the
pipeline may already have stored them.

Written before message_to_out or the download rule know about any of this.
"""

import uuid

import pytest
from sqlalchemy import event

import app.ws as ws_module
from app.db.models import User as DbUser
from app.db.renderings import set_message_pivot_text, set_message_transcript, upsert_rendering
from app.db.repository import create_media_object, create_message, set_message_status
from app.media_storage import get_media_storage
from app.messages import fan_out_message


def _user(db_session, name):
    user = DbUser(name=name, preferred_language="hi", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _audio(db_session, author_id, *, store=True):
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author_id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=3100,
    )
    db_session.commit()
    if store:
        get_media_storage().put(str(media.id), b"rendered-speech")
    return media.id


def _voice_dm(db_session, alice, bob, *, status="sent", with_audio=True):
    """A Telugu voice note alice -> bob, with the pipeline's output already
    stored (written while still pending, as the orchestrator will)."""
    original, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=alice.id,
        format="webm_opus",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=4200,
    )
    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="voice",
        original_media_ref=f"media:{original.id}",
        media_object_id=original.id,
        media_format="webm_opus",
        media_duration_ms=4200,
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    message_id = message.id
    audio_id = _audio(db_session, alice.id) if with_audio else None
    set_message_transcript(db_session, message_id, "ఈ రోజు సత్సంగం ఎప్పుడు?", "te")
    set_message_pivot_text(db_session, message_id, "When is satsang today?")
    upsert_rendering(
        db_session,
        message_id=message_id,
        language="hi",
        text="आज सत्संग कब होगा?",
        audio_media_object_id=audio_id,
        model_version_translate="indictrans2",
        model_version_tts="piper:hi_IN-rohan-medium",
    )
    upsert_rendering(
        db_session,
        message_id=message_id,
        language="en",
        text="When is satsang today?",
        degraded_reason="text_only",
    )
    db_session.commit()
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return message_id, audio_id


def _fetch(client, login_as, user, target_id):
    login_as(user)
    resp = client.get("/messages", params={"target_type": "user", "target_id": str(target_id)})
    assert resp.status_code == 200
    return resp.json()["messages"]


# --- GET /messages -----------------------------------------------------------------


def test_a_delivered_voice_note_arrives_with_its_transcript_and_renderings(
    client, db_session, login_as
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id, audio_id = _voice_dm(db_session, alice, bob, status="sent")

    (msg,) = _fetch(client, login_as, bob, alice.id)

    assert msg["id"] == str(message_id)
    assert msg["transcript"] == "ఈ రోజు సత్సంగం ఎప్పుడు?"
    assert msg["transcript_language"] == "te"
    by_lang = {r["language"]: r for r in msg["renderings"]}
    assert set(by_lang) == {"hi", "en"}
    assert by_lang["hi"]["text"] == "आज सत्संग कब होगा?"
    assert by_lang["hi"]["audio"] == {
        "uri": f"media:{audio_id}",
        "format": "wav_pcm16",
        "duration_ms": 3100,
    }
    assert by_lang["hi"]["degraded_reason"] is None
    assert by_lang["en"]["audio"] is None
    assert by_lang["en"]["degraded_reason"] == "text_only"
    # The original is still there, one tap away.
    assert msg["media_ref"]["format"] == "webm_opus"


@pytest.mark.parametrize("status", ["pending", "held", "blocked", "cancelled"])
def test_a_message_that_is_not_out_has_no_renderings_or_transcript_on_the_wire(
    client, db_session, login_as, status
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _voice_dm(db_session, alice, bob, status=status)

    (msg,) = _fetch(client, login_as, alice, bob.id)  # even the author's own view

    assert msg["renderings"] == []
    assert msg["transcript"] is None and msg["transcript_language"] is None


def test_a_page_of_messages_attaches_each_ones_own_renderings(client, db_session, login_as):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    first, _ = _voice_dm(db_session, alice, bob)
    second = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="no pipeline output",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    set_message_status(db_session, second.id, new_status="sent", expected="pending")
    db_session.commit()

    by_id = {m["id"]: m for m in _fetch(client, login_as, bob, alice.id)}

    assert len(by_id[str(first)]["renderings"]) == 2
    assert by_id[str(second.id)]["renderings"] == []


def test_a_page_of_messages_costs_one_renderings_query_not_one_per_message(
    client, db_session, login_as
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    for _ in range(5):
        _voice_dm(db_session, alice, bob)
    login_as(bob)
    seen: list[str] = []

    def count(conn, cursor, statement, *args):
        if "message_renderings" in statement and statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        resp = client.get("/messages", params={"target_type": "user", "target_id": str(alice.id)})
    finally:
        event.remove(engine, "before_cursor_execute", count)

    assert len(resp.json()["messages"]) == 5
    assert len(seen) == 1, f"expected one batched query, saw {len(seen)}"


# --- WebSocket ---------------------------------------------------------------------


def test_a_ws_sync_batch_carries_renderings_too(client, db_session, ws_login_as):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id, _ = _voice_dm(db_session, alice, bob)
    token = ws_login_as(bob)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json(
            {
                "type": "sync.request",
                "data": {"target_type": "user", "target_id": str(alice.id)},
            }
        )
        batch = ws.receive_json()

    (msg,) = batch["data"]["messages"]
    assert msg["id"] == str(message_id)
    assert {r["language"] for r in msg["renderings"]} == {"hi", "en"}
    assert msg["transcript_language"] == "te"


async def test_the_message_new_frame_pushed_at_delivery_carries_them(
    db_session, engine, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id, _ = _voice_dm(db_session, alice, bob, status="pending")
    sent_frames = []

    async def capture(recipients, frame, exclude=None):
        sent_frames.append(frame)

    monkeypatch.setattr(ws_module.manager, "broadcast", capture)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    await fan_out_message(message_id, ws_module.manager, factory)

    (frame,) = [f for f in sent_frames if f["type"] == "message.new"]
    assert frame["data"]["status"] == "sent"
    assert {r["language"] for r in frame["data"]["renderings"]} == {"hi", "en"}
    assert frame["data"]["transcript"] == "ఈ రోజు సత్సంగం ఎప్పుడు?"


# --- the audio a rendering points at must be fetchable by who it was made for --------------


def test_the_recipient_can_fetch_a_renderings_audio_once_the_message_is_out(
    client, db_session, login_as
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _, audio_id = _voice_dm(db_session, alice, bob, status="sent")
    login_as(bob)

    resp = client.get(f"/media/{audio_id}")

    assert resp.status_code == 200
    assert resp.content == b"rendered-speech"


def test_a_stranger_cannot_fetch_a_renderings_audio(client, db_session, login_as):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    mallory = _user(db_session, "Mallory")
    _, audio_id = _voice_dm(db_session, alice, bob, status="sent")
    login_as(mallory)

    refused = client.get(f"/media/{audio_id}")
    missing = client.get(f"/media/{uuid.uuid4()}")

    assert refused.status_code == 404
    assert refused.json() == missing.json()


@pytest.mark.parametrize("status", ["pending", "held", "blocked", "cancelled"])
def test_the_recipient_cannot_fetch_a_renderings_audio_before_the_message_is_out(
    client, db_session, login_as, status
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _, audio_id = _voice_dm(db_session, alice, bob, status=status)
    login_as(bob)

    assert client.get(f"/media/{audio_id}").status_code == 404


def test_a_circle_member_can_fetch_a_renderings_audio(client, db_session, login_as):
    from app.db.repository import add_member, create_circle

    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    circle = create_circle(db_session, name="Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    db_session.commit()
    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="circle",
        target_circle_id=circle.id,
        kind="text",
        text="namaste",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    audio_id = _audio(db_session, alice.id)
    upsert_rendering(
        db_session,
        message_id=message.id,
        language="hi",
        text="नमस्ते",
        audio_media_object_id=audio_id,
    )
    db_session.commit()
    set_message_status(db_session, message.id, new_status="sent", expected="pending")
    db_session.commit()
    login_as(bob)

    assert client.get(f"/media/{audio_id}").status_code == 200
