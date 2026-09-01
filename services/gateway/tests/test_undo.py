"""Tests for Phase 8's 30-second undo window: POST /messages (and the WS
message.send path) create a message in `pending` status; real delivery is
deferred via app/undo.py's in-memory scheduler (`fan_out_message`, run after
a delay); DELETE /messages/{id} cancels that deferred delivery within the
window.

Written before app/undo.py, DELETE /messages/{id}, and
app.messages.fan_out_message exist — collecting this file will fail with an
ImportError/AttributeError until Step 2/3's implementation lands, matching
the tests-first precedent tests/test_message_routes.py and
tests/test_ws_delivery.py already established for this repo (see either
file's own module docstring).

How these tests avoid a real 30-second wait, in order of preference:

1. Most tests (all of Part A except the two below) call
   `app.messages.fan_out_message(...)` directly as a coroutine —
   `asyncio_mode = "auto"` (pyproject.toml) lets a plain `async def` test
   function await it with no decorator. This exercises fan_out_message's
   own "only deliver a still-pending message" logic without touching
   app/undo.py's timer at all, and sidesteps a real cross-thread timing
   problem: TestClient runs the ASGI app's async routes on a separate
   worker thread's own event loop, so a background asyncio.Task an HTTP
   or WS handler schedules there isn't something a synchronous test
   function on the main thread can simply await or fast-forward.

2. `test_ws_fan_out_after_delay_sends_message_new` (Part B) is the one
   test that has to prove the real scheduled path — app/undo.py's actual
   timer — works end to end. It monkeypatches `app.undo.asyncio_sleep`
   (the name app/undo.py imports `asyncio.sleep` as into its own
   namespace, not the global `asyncio.sleep` every other coroutine in the
   test — including TestClient's own WS transport — might depend on; same
   "patch the name as imported into that module's own namespace" pattern
   tests/conftest.py's `ws_login_as` fixture already documents for
   `app.ws.user_from_token`) so the wait collapses to nothing, then relies
   on `bob_ws.receive_json()` blocking naturally until the now-instant
   background task actually runs — no arbitrary real-time sleep in the
   test itself.

Both the HTTP and WS send paths open their own DB session for the deferred
fan-out (`app.messages.SessionLocal` / `app.ws.SessionLocal`, both imported
from app.db.base) rather than reusing the request/connection's own session,
since a background task can easily outlive either. That production
SessionLocal is bound to whatever `.env`'s DATABASE_URL is — not
necessarily this test suite's separate TEST_DATABASE_URL — so any test that
needs the *real* scheduled task to reach the test database monkeypatches
that name too, same pattern.

Tests that bypass the scheduler by calling fan_out_message directly always
pass `lambda: db_session` as its session_factory instead, for the same
reason.
"""

import uuid

from sqlalchemy import select

from app.db.models import Message
from app.db.models import User as DbUser
from app.messages import fan_out_message
from app.ws import ConnectionManager


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _post_message(client, *, target_type, target_id, text, client_msg_id=None):
    return client.post(
        "/messages",
        json={
            "client_msg_id": client_msg_id or str(uuid.uuid4()),
            "target_type": target_type,
            "target_id": target_id,
            "kind": "text",
            "text": text,
        },
    )


def _send_frame(*, client_msg_id, target_type, target_id, text):
    return {
        "type": "message.send",
        "data": {
            "client_msg_id": client_msg_id,
            "target_type": target_type,
            "target_id": target_id,
            "kind": "text",
            "text": text,
        },
    }


def _row_status(db_session, message_id: str) -> str:
    db_session.expire_all()
    row = db_session.execute(
        select(Message).where(Message.id == uuid.UUID(message_id))
    ).scalar_one()
    return row.status


def _recording_manager():
    """A real ConnectionManager whose broadcast() records calls instead of
    touching any actual sockets — for tests that call fan_out_message
    directly and need to inspect what it would have sent, without a real
    WS connection in play."""
    manager = ConnectionManager()
    calls: list[tuple[list[str], dict]] = []

    async def fake_broadcast(user_ids, frame, *, exclude=None):
        calls.append((user_ids, frame))

    manager.broadcast = fake_broadcast
    return manager, calls


# --- Part A: the 30-second undo, HTTP path ----------------------------------


def test_create_message_status_is_pending(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="hi Bob")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_message_not_delivered_while_pending(client, db_session, login_as, monkeypatch):
    import app.ws as ws_module

    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    broadcast_calls = []

    async def fake_broadcast(user_ids, frame, *, exclude=None):
        broadcast_calls.append((user_ids, frame))

    monkeypatch.setattr(ws_module.manager, "broadcast", fake_broadcast)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="hi Bob")

    assert response.status_code == 200
    # The real fan-out was scheduled 30 real seconds out (default
    # UNDO_WINDOW_SECONDS) — this test doesn't wait for it, it only proves
    # nothing was sent synchronously as part of handling the POST itself.
    assert broadcast_calls == []


def test_undo_within_window_returns_204(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="oops")
    message_id = response.json()["id"]

    delete_response = client.delete(f"/messages/{message_id}")

    assert delete_response.status_code == 204
    assert _row_status(db_session, message_id) == "cancelled"


async def test_undo_after_window_returns_409(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="oops")
    message_id = response.json()["id"]

    # Fast-forward past the undo window: run the deferred fan-out directly
    # instead of waiting out a real 30-second delay (see module docstring,
    # approach 1) — the window has "closed" once fan-out has actually run,
    # regardless of how much wall-clock time that took.
    manager, _calls = _recording_manager()
    await fan_out_message(uuid.UUID(message_id), manager, lambda: db_session)
    assert _row_status(db_session, message_id) == "sent"

    delete_response = client.delete(f"/messages/{message_id}")

    assert delete_response.status_code == 409
    assert "detail" in delete_response.json()
    assert delete_response.json()["detail"]


def test_undo_by_wrong_user_returns_403(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="hi Bob")
    message_id = response.json()["id"]

    login_as(bob)
    delete_response = client.delete(f"/messages/{message_id}")

    assert delete_response.status_code == 403
    assert _row_status(db_session, message_id) == "pending"


async def test_fan_out_occurs_after_delay(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="hi Bob")
    message_id = response.json()["id"]

    manager, calls = _recording_manager()
    await fan_out_message(uuid.UUID(message_id), manager, lambda: db_session)

    assert len(calls) == 1
    recipients, frame = calls[0]
    assert frame["type"] == "message.new"
    assert frame["data"]["id"] == message_id
    assert frame["data"]["status"] == "sent"
    assert set(recipients) == {str(alice.id), str(bob.id)}

    assert _row_status(db_session, message_id) == "sent"


async def test_undo_cancels_pending_task(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    response = _post_message(client, target_type="user", target_id=str(bob.id), text="oops")
    message_id = response.json()["id"]

    delete_response = client.delete(f"/messages/{message_id}")
    assert delete_response.status_code == 204

    # The 30-second real task the POST scheduled never fired (this test
    # runs in milliseconds) — this proves the *other* half: if it somehow
    # ran anyway, fan_out_message's own pending-status guard makes it a
    # no-op, not just that nothing has fired yet.
    manager, calls = _recording_manager()
    await fan_out_message(uuid.UUID(message_id), manager, lambda: db_session)

    assert calls == []
    assert _row_status(db_session, message_id) == "cancelled"


def test_idempotent_send_still_pending(client, db_session, login_as):
    import app.undo as undo_module

    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    shared_client_msg_id = str(uuid.uuid4())
    first = _post_message(
        client,
        target_type="user",
        target_id=str(bob.id),
        text="hi Bob",
        client_msg_id=shared_client_msg_id,
    )
    second = _post_message(
        client,
        target_type="user",
        target_id=str(bob.id),
        text="retried send after dropped ack",
        client_msg_id=shared_client_msg_id,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["status"] == "pending"

    # No duplicate task: schedule_fan_out is keyed by message_id, so a
    # dedup retry that recovers the same row must not grow app/undo.py's
    # registry a second time for it.
    assert list(undo_module._pending).count(first.json()["id"]) <= 1

    rows = (
        db_session.execute(
            select(Message).where(Message.client_msg_id == uuid.UUID(shared_client_msg_id))
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


# --- Part B: the 30-second undo, WebSocket path ------------------------------


def test_ws_message_send_ack_status_pending(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )
        ack = alice_ws.receive_json()
        assert ack["type"] == "message.ack"
        assert ack["data"]["status"] == "pending"


def test_ws_fan_out_after_delay_sends_message_new(client, db_session, ws_login_as, monkeypatch):
    import app.undo as undo_module
    import app.ws as ws_module

    # Real scheduler, real asyncio.Task — collapse the wait and point its
    # independent session at the test database (see module docstring,
    # approach 2).
    monkeypatch.setattr(ws_module, "SessionLocal", lambda: db_session)

    async def instant_sleep(_delay):
        return None

    monkeypatch.setattr(undo_module, "asyncio_sleep", instant_sleep)

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
        assert ack["data"]["status"] == "pending"

        # Blocks until the (now near-instant) scheduled fan-out actually
        # runs and broadcasts — no arbitrary real-time sleep needed here.
        new = bob_ws.receive_json()
        assert new["type"] == "message.new"
        assert new["data"]["id"] == ack["data"]["id"]
        assert new["data"]["status"] == "sent"

    assert _row_status(db_session, ack["data"]["id"]) == "sent"


async def test_ws_undo_via_http_cancels_ws_message(
    client, db_session, ws_login_as, login_as, monkeypatch
):
    import app.ws as ws_module

    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_ws_token = ws_login_as(alice)
    bob_ws_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_ws_token}") as alice_ws,
        # Bob just needs to be connected (a real recipient for the
        # would-be broadcast) — nothing is ever read from his socket, since
        # the whole point is that nothing should ever arrive on it.
        client.websocket_connect(f"/ws?token={bob_ws_token}"),
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="oops",
            )
        )
        ack = alice_ws.receive_json()
        message_id = ack["data"]["id"]

        # HTTP identity — a separate dependency-override seam from
        # ws_login_as (see conftest.py) — same author, authenticated over
        # HTTP to hit the DELETE route.
        login_as(alice)
        delete_response = client.delete(f"/messages/{message_id}")
        assert delete_response.status_code == 204

    # The WS-scheduled fan-out's real 30s delay never elapsed during this
    # test — confirm the HTTP undo really cancelled it, not merely raced
    # it, by running the exact coroutine app/undo.py would have run and
    # checking it's a no-op (same technique as test_undo_cancels_pending_task).
    broadcast_calls = []

    async def fake_broadcast(user_ids, frame, *, exclude=None):
        broadcast_calls.append((user_ids, frame))

    monkeypatch.setattr(ws_module.manager, "broadcast", fake_broadcast)

    await fan_out_message(uuid.UUID(message_id), ws_module.manager, lambda: db_session)

    assert broadcast_calls == []
    assert _row_status(db_session, message_id) == "cancelled"
