"""A message that is not OUT must not reach anyone but its author.

`get_messages_since` (the query behind `GET /messages` and the WebSocket
`sync.request`) returned every non-deleted message in the conversation
whatever its status and whoever asked. So a recipient who synced saw -- text,
`media_ref` and all -- a message still inside its 30-second undo window, one a
moderator had HELD, one BLOCKED, one the sender had cancelled. That breaks two
stated requirements at once:

- docs/security-checklist.md: "Undo really unsends: the message is gone from
  receivers' clients";
- moderation itself: a held or blocked message exists to be withheld, and the
  only thing that kept it from the recipient was the fan-out, not the read
  path.

Rule: the AUTHOR sees their own messages in every status (their screen shows
"Sending...", "Cancelled", "Held"); everyone else sees only `sent` /
`delivered`. Applied in the query -- before the page limit, so a hidden row
can't leave a short page that looks like the end of history.

Written before the visible-messages query exists.
"""

import uuid

import pytest

from app.db.models import User as DbUser
from app.db.repository import (
    add_member,
    create_circle,
    create_message,
    set_message_status,
)

NOT_OUT = ["pending", "held", "blocked", "cancelled", "failed"]
OUT = ["sent", "delivered"]


def _user(db_session, name):
    user = DbUser(name=name, preferred_language="en", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _dm(db_session, author, target, status, text="hello"):
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="text",
        text=text,
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    message_id = message.id
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return message_id


def _http(client, login_as, viewer, params):
    login_as(viewer)
    resp = client.get("/messages", params=params)
    assert resp.status_code == 200
    return resp.json()


def _ws_sync(client, ws_login_as, viewer, data):
    token = ws_login_as(viewer)
    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json({"type": "sync.request", "data": data})
        frame = ws.receive_json()
    assert frame["type"] == "sync.batch"
    return frame["data"]


# --- DM: the recipient ------------------------------------------------------------------


@pytest.mark.parametrize("status", NOT_OUT)
def test_a_recipient_does_not_see_a_message_that_is_not_out_over_http(
    client, db_session, login_as, status
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _dm(db_session, alice, bob, status, text="the secret words")

    body = _http(client, login_as, bob, {"target_type": "user", "target_id": str(alice.id)})

    assert body["messages"] == []
    assert "the secret words" not in str(body)


@pytest.mark.parametrize("status", NOT_OUT)
def test_a_recipient_does_not_see_a_message_that_is_not_out_over_the_websocket(
    client, db_session, ws_login_as, status
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _dm(db_session, alice, bob, status, text="the secret words")

    data = _ws_sync(client, ws_login_as, bob, {"target_type": "user", "target_id": str(alice.id)})

    assert data["messages"] == []


@pytest.mark.parametrize("status", OUT)
def test_a_recipient_sees_a_message_that_is_out(client, db_session, login_as, status):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status)

    body = _http(client, login_as, bob, {"target_type": "user", "target_id": str(alice.id)})

    assert [m["id"] for m in body["messages"]] == [str(message_id)]


# --- the author is unaffected -------------------------------------------------------------


@pytest.mark.parametrize("status", NOT_OUT + OUT)
def test_the_author_still_sees_their_own_message_in_every_status(
    client, db_session, login_as, status
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status)

    body = _http(client, login_as, alice, {"target_type": "user", "target_id": str(bob.id)})

    assert [(m["id"], m["status"]) for m in body["messages"]] == [(str(message_id), status)]


def test_the_author_sees_their_own_in_a_ws_sync_too(client, db_session, ws_login_as):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, "held")

    data = _ws_sync(client, ws_login_as, alice, {"target_type": "user", "target_id": str(bob.id)})

    assert [m["id"] for m in data["messages"]] == [str(message_id)]


def test_each_side_of_a_conversation_sees_their_own_view(client, db_session, login_as):
    """Both directions in one thread: Alice's held message is invisible to Bob;
    Bob's delivered reply is visible to Alice; Alice still sees her own."""
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    held = _dm(db_session, alice, bob, "held")
    reply = _dm(db_session, bob, alice, "sent")

    for_bob = _http(client, login_as, bob, {"target_type": "user", "target_id": str(alice.id)})
    for_alice = _http(client, login_as, alice, {"target_type": "user", "target_id": str(bob.id)})

    assert [m["id"] for m in for_bob["messages"]] == [str(reply)]
    assert {m["id"] for m in for_alice["messages"]} == {str(held), str(reply)}


def test_a_released_message_becomes_visible_to_the_recipient(client, db_session, login_as):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, "held")
    assert (
        _http(client, login_as, bob, {"target_type": "user", "target_id": str(alice.id)})[
            "messages"
        ]
        == []
    )

    set_message_status(db_session, message_id, new_status="sent", expected="held")
    db_session.commit()

    body = _http(client, login_as, bob, {"target_type": "user", "target_id": str(alice.id)})
    assert [m["id"] for m in body["messages"]] == [str(message_id)]


# --- circles ---------------------------------------------------------------------------------


def _circle(db_session, author, *members):
    circle = create_circle(db_session, name="Satsang", created_by=author.id)
    add_member(db_session, circle_id=circle.id, user_id=author.id, role="admin")
    for member in members:
        add_member(db_session, circle_id=circle.id, user_id=member.id)
    db_session.commit()
    return circle


def _circle_message(db_session, author, circle, status):
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="circle",
        target_circle_id=circle.id,
        kind="text",
        text="to the circle",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    message_id = message.id
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return message_id


@pytest.mark.parametrize("status", NOT_OUT)
def test_a_circle_member_does_not_see_a_message_that_is_not_out(
    client, db_session, login_as, status
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    circle = _circle(db_session, alice, bob)
    _circle_message(db_session, alice, circle, status)

    body = _http(client, login_as, bob, {"target_type": "circle", "target_id": str(circle.id)})

    assert body["messages"] == []


def test_a_circle_member_sees_a_sent_message_and_the_author_sees_all_of_theirs(
    client, db_session, login_as
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    circle = _circle(db_session, alice, bob)
    sent = _circle_message(db_session, alice, circle, "sent")
    held = _circle_message(db_session, alice, circle, "held")

    for_bob = _http(client, login_as, bob, {"target_type": "circle", "target_id": str(circle.id)})
    for_alice = _http(
        client, login_as, alice, {"target_type": "circle", "target_id": str(circle.id)}
    )

    assert [m["id"] for m in for_bob["messages"]] == [str(sent)]
    assert {m["id"] for m in for_alice["messages"]} == {str(sent), str(held)}


# --- paging ------------------------------------------------------------------------------------


def test_hidden_messages_do_not_shorten_a_page_or_fake_the_end_of_history(
    client, db_session, login_as
):
    """Five messages; the recipient may see only 1, 3 and 5. The filter must run
    BEFORE the page limit: a page of 2 holds the first two VISIBLE messages and
    says more remain; filtering afterwards would return a short page and
    has_more=False and silently drop the rest."""
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    statuses = ["sent", "held", "sent", "held", "sent"]
    ids = [_dm(db_session, alice, bob, s) for s in statuses]
    # UUIDv7 only orders across distinct milliseconds; sort for a stable expectation.
    ordered = sorted(zip(ids, statuses))
    visible = [str(i) for i, s in ordered if s == "sent"]

    page1 = _http(
        client, login_as, bob, {"target_type": "user", "target_id": str(alice.id), "limit": 2}
    )
    assert [m["id"] for m in page1["messages"]] == visible[:2]
    assert page1["has_more"] is True

    page2 = _http(
        client,
        login_as,
        bob,
        {
            "target_type": "user",
            "target_id": str(alice.id),
            "limit": 2,
            "since_id": page1["messages"][-1]["id"],
        },
    )
    assert [m["id"] for m in page2["messages"]] == visible[2:]
    assert page2["has_more"] is False
