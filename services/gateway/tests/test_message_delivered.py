"""Tests for the `message.delivered` WS frame — the second half of Week 3's
"sent / delivered states" requirement. `SENT` (the server pushed a message
out) already existed via app/undo.py's deferred fan-out; this file covers
the still-missing half: a *recipient* confirming actual receipt, and the
*sender* being told about it.

Originally DM-only, deliberately — "delivered to whom" for a multi-
recipient circle message was a genuinely harder, separate design question,
flagged as an explicit open gap (see the DM-only tests above this comment,
still here and unchanged). That gap is closed below once circles became a
real, working feature (self-join, #32): each circle member confirms their
own delivery independently, and the sender is told an aggregate
delivered_count/member_count rather than a single delivered/not-delivered
boolean — see contracts/chat/messages.py's MessageStatusOut docstring for
the full reasoning on why that's a count, not a status value.
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


def test_circle_member_delivered_ack_reports_aggregate_count(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    carol = _make_db_user(db_session, "Carol")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)
    carol_token = ws_login_as(carol)
    circle = create_circle(db_session, name="Family", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=bob.id)
    add_member(db_session, circle_id=circle.id, user_id=carol.id)
    db_session.commit()

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
        client.websocket_connect(f"/ws?token={carol_token}") as carol_ws,
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
        carol_ws.receive_json()  # message.new

        bob_ws.send_json(_deliver_frame(message_id=message_id))
        first_update = alice_ws.receive_json()
        assert first_update["type"] == "message.status"
        assert first_update["data"]["id"] == message_id
        # member_count excludes the author (Alice) — 2 real recipients
        # (Bob, Carol), matching _handle_message_delivered_circle's own
        # "- 1" reasoning, not a hardcoded expectation.
        assert first_update["data"]["member_count"] == 2
        assert first_update["data"]["delivered_count"] == 1
        # status is NOT overwritten to "delivered" for a circle message —
        # it's still whatever the real sender-side lifecycle value is.
        assert first_update["data"]["status"] == "sent"

        carol_ws.send_json(_deliver_frame(message_id=message_id))
        second_update = alice_ws.receive_json()
        assert second_update["type"] == "message.status"
        assert second_update["data"]["delivered_count"] == 2
        assert second_update["data"]["member_count"] == 2

    row = db_session.execute(
        select(Message).where(Message.id == uuid.UUID(message_id))
    ).scalar_one()
    # messages.status genuinely untouched by circle delivery tracking —
    # confirms MessageStatusOut's echoed "sent" above reflects real DB
    # state, not just what the frame happened to report.
    assert row.status == "sent"


def test_circle_delivered_ack_from_non_member_is_rejected(
    client, db_session, ws_login_as, _instant_fan_out
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    eve = _make_db_user(db_session, "Eve")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)
    eve_token = ws_login_as(eve)
    circle = create_circle(db_session, name="Family", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=bob.id)
    db_session.commit()

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
        client.websocket_connect(f"/ws?token={eve_token}") as eve_ws,
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

        # Eve is a real, authenticated user — just not a member of this
        # circle. She must not be able to confirm delivery of a message
        # she was never sent.
        eve_ws.send_json(_deliver_frame(message_id=message_id))
        response = eve_ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["code"] == "UNAUTHORIZED"


def test_circle_author_cannot_confirm_their_own_message(
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

        alice_ws.send_json(_deliver_frame(message_id=message_id))
        response = alice_ws.receive_json()
        assert response["type"] == "error"
        assert response["data"]["code"] == "VALIDATION_FAILED"


def test_circle_duplicate_delivered_ack_is_idempotent(
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
        first_update = alice_ws.receive_json()
        assert first_update["data"]["delivered_count"] == 1

        # A second ack for the same message from the same member (e.g. a
        # second device, or a client retry) must not error and must not
        # push a second redundant status update — same ordered-stream
        # sentinel proof test_duplicate_delivered_ack_is_idempotent above
        # uses for the DM case.
        bob_ws.send_json(_deliver_frame(message_id=message_id))

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
