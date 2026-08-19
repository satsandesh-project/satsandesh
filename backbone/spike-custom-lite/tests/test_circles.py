"""Week 3, Step 4: "post to a circle works".

Same in-process TestClient pattern as test_delivery.py -- app.py's
lifespan starts the real dispatcher against real Postgres, so these
exercise actual delivery, not a mock of it.

On proving the negatives (4.2 and 4.4): "nothing arrived on this socket"
and "nothing has arrived *yet*" are indistinguishable over a websocket,
so asserting non-delivery by waiting and hoping would be timing-
dependent and quietly flaky. These instead assert the underlying
invariant directly in Postgres: that no delivery obligation (spike_outbox
row) was ever created for that user. That tests the cause rather than a
symptom, and is deterministic.

Note what is NOT retested here: ordering, crash safety, and SKIP LOCKED
concurrency. Circles reuse the Week 2 outbox unchanged, so those remain
covered by test_delivery.py / test_crash_safety.py / test_concurrency.py.
Re-asserting them here would imply circles changed delivery, which is
exactly what this design avoids.
"""

import time

import psycopg
from starlette.testclient import TestClient

from app import app
from db import DATABASE_URL


def _create_circle(client, name="Sunday Satsang"):
    resp = client.post("/circles", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["circle_id"]


def _add(client, circle_id, user_id):
    resp = client.post(f"/circles/{circle_id}/members", json={"user_id": user_id})
    assert resp.status_code == 200, resp.text


def _announce(client, circle_id, sender_id, body):
    resp = client.post(
        f"/circles/{circle_id}/announce", json={"sender_id": sender_id, "body": body}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["message_id"]


def _outbox_bodies_for(recipient_id):
    """Every message body currently owed to (or already delivered to) this
    recipient. Sync psycopg on purpose -- test-side assertion, no event
    loop involved."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT m.body FROM spike_outbox o "
                "JOIN spike_messages m ON m.id = o.message_id "
                "WHERE o.recipient_id = %s ORDER BY o.id",
                (recipient_id,),
            )
            return [r[0] for r in cur.fetchall()]


def test_announcement_reaches_all_three_members(spike_clean_db):
    """Step 4.1: create a circle, add 3 members, post once -> all 3 get it."""
    with TestClient(app) as client:
        circle_id = _create_circle(client)
        for user in ("bob", "carol", "dave"):
            _add(client, circle_id, user)

        assert client.get(f"/circles/{circle_id}/members").json()["members"] == [
            "bob",
            "carol",
            "dave",
        ]

        with client.websocket_connect("/ws?user_id=bob") as ws_bob, \
             client.websocket_connect("/ws?user_id=carol") as ws_carol, \
             client.websocket_connect("/ws?user_id=dave") as ws_dave:
            _announce(client, circle_id, "alice", "satsang at 6pm")

            for ws in (ws_bob, ws_carol, ws_dave):
                msg = ws.receive_json()
                assert msg["body"] == "satsang at 6pm"
                assert msg["sender_id"] == "alice"
                assert msg["conversation_id"] == circle_id


def test_non_member_does_not_receive(spike_clean_db):
    """Step 4.2: a user outside the circle gets nothing."""
    with TestClient(app) as client:
        circle_id = _create_circle(client)
        _add(client, circle_id, "bob")

        with client.websocket_connect("/ws?user_id=bob") as ws_bob:
            _announce(client, circle_id, "alice", "members only")
            # Positive confirmation the announcement really was processed.
            assert ws_bob.receive_json()["body"] == "members only"

        assert _outbox_bodies_for("bob") == ["members only"]
        # Never any obligation to a non-member in the first place.
        assert _outbox_bodies_for("mallory") == []


def test_offline_member_gets_it_on_reconnect(spike_clean_db):
    """Step 4.3: member offline at post time still gets it on reconnect.

    This should fall straight out of the Week 2 outbox -- an announcement
    writes an outbox row per member regardless of who's connected, and
    the dispatcher leaves rows pending while a recipient is offline. If
    this ever fails, that's a real finding about circles breaking the
    outbox guarantee, not a test to relax.
    """
    with TestClient(app) as client:
        circle_id = _create_circle(client)
        _add(client, circle_id, "bob")
        _add(client, circle_id, "offline_olive")

        with client.websocket_connect("/ws?user_id=bob") as ws_bob:
            _announce(client, circle_id, "alice", "you were away")
            assert ws_bob.receive_json()["body"] == "you were away"

        # olive was never connected for that. Let the dispatcher cycle a
        # few times so this can't pass just by racing ahead of it.
        time.sleep(0.6)

        with client.websocket_connect("/ws?user_id=offline_olive") as ws_olive:
            assert ws_olive.receive_json()["body"] == "you were away"


def test_removed_member_stops_receiving_but_keeps_delivered(spike_clean_db):
    """Step 4.4: removal stops *future* announcements, without disturbing
    what was already owed.

    The second half matters as much as the first: removal must not
    retroactively cancel a message already written for delivery. See
    interfaces.py's post_announcement contract -- recipients are resolved
    at post time, and that resolution is the obligation.
    """
    with TestClient(app) as client:
        circle_id = _create_circle(client)
        _add(client, circle_id, "bob")
        _add(client, circle_id, "leaving_larry")

        # Posted while Larry is still a member -- so it's owed to him,
        # even though he's offline right now.
        _announce(client, circle_id, "alice", "before removal")

        client.delete(f"/circles/{circle_id}/members/leaving_larry")
        assert client.get(f"/circles/{circle_id}/members").json()["members"] == ["bob"]

        with client.websocket_connect("/ws?user_id=bob") as ws_bob:
            _announce(client, circle_id, "alice", "after removal")
            bodies = {ws_bob.receive_json()["body"] for _ in range(2)}
            assert bodies == {"before removal", "after removal"}

        # Larry: owed exactly the pre-removal message, and never the
        # post-removal one.
        assert _outbox_bodies_for("leaving_larry") == ["before removal"]

        # And he still actually receives it on reconnect -- removal
        # didn't cancel the outstanding obligation.
        with client.websocket_connect("/ws?user_id=leaving_larry") as ws_larry:
            assert ws_larry.receive_json()["body"] == "before removal"


def test_list_messages_returns_history_newest_first(spike_clean_db):
    """Not one of the four required, but list_messages is part of the
    interface, so it needs at least one real check."""
    with TestClient(app) as client:
        circle_id = _create_circle(client)
        _add(client, circle_id, "bob")
        for i in range(3):
            _announce(client, circle_id, "alice", f"m{i}")

        messages = client.get(f"/circles/{circle_id}/messages").json()["messages"]
        assert [m["body"] for m in messages] == ["m2", "m1", "m0"]
        assert all(m["circle_id"] == circle_id for m in messages)

        # Paging backwards from the newest returns the two older ones.
        older = client.get(
            f"/circles/{circle_id}/messages", params={"before": messages[0]["id"]}
        ).json()["messages"]
        assert [m["body"] for m in older] == ["m1", "m0"]
