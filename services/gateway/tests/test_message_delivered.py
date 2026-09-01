"""Tests for the `message.delivered` WS frame — the second half of Week 3's
"sent / delivered states" requirement. `SENT` (the server pushed a message
out) already existed via app/undo.py's deferred fan-out; this file covers
the still-missing half: the *recipient* confirming actual receipt, and the
*sender* being told about it.

Scoped to DMs only, deliberately — the task this closes
("1:1 text messaging... sent/delivered states") is explicitly about DMs,
and "delivered to whom" for a multi-recipient circle message is a separate,
harder design question (first recipient? all of them?) that this file
doesn't try to answer. test_delivered_ack_for_circle_target_is_rejected
below locks that scope decision in as a test, not just a comment.

Written before app/ws.py's handler exists, same tests-first precedent
tests/test_ws_delivery.py documents for message.send: collecting this file
should succeed once FrameType.MESSAGE_DELIVERED/contracts/chat/messages.py's
DeliveredIn exist, but every test here is expected to fail until the
handler itself lands.
"""

import uuid

from sqlalchemy import select

from app.db.models import Message
from app.db.repository import add_member, create_circle

from .test_ws_delivery import _make_db_user, _send_frame


def _deliver_frame(*, message_id):
    return {"type": "message.delivered", "data": {"message_id": message_id}}


def test_recipient_can_mark_dm_delivered_and_sender_is_notified(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )
        ack = alice_ws.receive_json()
        message_id = ack["data"]["id"]
        new = bob_ws.receive_json()
        assert new["type"] == "message.new"

        bob_ws.send_json(_deliver_frame(message_id=message_id))

        status_update = alice_ws.receive_json()
        assert status_update["type"] == "message.status"
        assert status_update["data"]["id"] == message_id
        assert status_update["data"]["status"] == "delivered"

    row = db_session.execute(
        select(Message).where(Message.id == uuid.UUID(message_id))
    ).scalar_one()
    assert row.status == "delivered"


def test_delivered_ack_from_non_recipient_is_rejected(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    eve = _make_db_user(db_session, "Eve")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)
    eve_token = ws_login_as(eve)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
        client.websocket_connect(f"/ws?token={eve_token}") as eve_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )
        ack = alice_ws.receive_json()
        message_id = ack["data"]["id"]
        bob_ws.receive_json()  # message.new, not under test here

        # Eve is a real, authenticated user — just not this message's
        # recipient. She must not be able to mark someone else's DM
        # delivered.
        eve_ws.send_json(_deliver_frame(message_id=message_id))
        response = eve_ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["code"] == "UNAUTHORIZED"

    row = db_session.execute(
        select(Message).where(Message.id == uuid.UUID(message_id))
    ).scalar_one()
    assert row.status == "sent"


def test_author_cannot_mark_their_own_message_delivered(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )
        ack = alice_ws.receive_json()
        message_id = ack["data"]["id"]
        bob_ws.receive_json()  # message.new

        alice_ws.send_json(_deliver_frame(message_id=message_id))
        response = alice_ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["code"] == "UNAUTHORIZED"


def test_duplicate_delivered_ack_is_idempotent(client, db_session, ws_login_as, _instant_fan_out):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )
        ack = alice_ws.receive_json()
        message_id = ack["data"]["id"]
        bob_ws.receive_json()  # message.new

        bob_ws.send_json(_deliver_frame(message_id=message_id))
        first_status = alice_ws.receive_json()
        assert first_status["type"] == "message.status"

        # A second ack for the same message (e.g. Bob has two devices, or
        # a client retry) must not error and must not push a second
        # redundant status update to Alice.
        bob_ws.send_json(_deliver_frame(message_id=message_id))

        # Prove no second message.status arrived: send a distinguishable
        # sentinel DM from Bob to Alice next, and confirm it's the very
        # next frame Alice's socket receives (same ordered-stream proof
        # tests/test_ws_delivery.py's failure-isolation test uses).
        sentinel_id = str(uuid.uuid4())
        bob_ws.send_json(
            _send_frame(
                client_msg_id=sentinel_id,
                target_type="user",
                target_id=str(alice.id),
                text="sentinel",
            )
        )
        next_frame = alice_ws.receive_json()
        assert next_frame["type"] == "message.new"
        assert next_frame["data"]["text"] == "sentinel"

    row = db_session.execute(
        select(Message).where(Message.id == uuid.UUID(message_id))
    ).scalar_one()
    assert row.status == "delivered"


def test_delivered_ack_for_circle_target_is_rejected(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)
    circle = create_circle(db_session, name="Family", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=bob.id)
    db_session.commit()

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="circle",
                target_id=str(circle.id),
                text="hi circle",
            )
        )
        ack = alice_ws.receive_json()
        message_id = ack["data"]["id"]
        bob_ws.receive_json()  # message.new

        bob_ws.send_json(_deliver_frame(message_id=message_id))
        response = bob_ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["code"] == "VALIDATION_FAILED"


def test_delivered_ack_for_nonexistent_message_is_rejected(
    client, db_session, ws_login_as, _instant_fan_out
):
    bob = _make_db_user(db_session, "Bob")
    bob_token = ws_login_as(bob)

    with client.websocket_connect(f"/ws?token={bob_token}") as bob_ws:
        bob_ws.send_json(_deliver_frame(message_id=str(uuid.uuid4())))
        response = bob_ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["code"] == "NOT_FOUND"
