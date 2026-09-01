"""WS sync tests for app/ws.py's `sync.request` -> `sync.batch` path (Week 3
Phase 6) — the offline-queue read side, on top of Phase 5's `message.send`
write side.

Path 1, confirmed in Step 0: this is built exactly per the shipped contract
(`contracts/chat/envelope.py::SyncRequest`/`SyncBatch`) — per-conversation,
resolved via the same `(target_type, target_id)` -> `conversation_id` path
`GET /messages` already uses (`find_conversation_id`/`is_circle_member`),
calling the existing `app/db/repository.py::get_messages_since` unchanged.
No new repository function, no contract change — see
docs/SCHEMA_DRAFT.md's messages-table index section, which states this
pair is backed by the same partial index as `GET /messages` "exactly as
before... no route or frame shape changes."

Uses `TestClient.websocket_connect` and the same `client`/`db_session`/
`ws_login_as` fixtures as tests/test_ws_delivery.py — see that file's
docstring for why `ws_login_as` (not HTTP's `login_as`) is needed for WS
auth. Messages are seeded directly via `app/db/repository.py::create_message`
(not over WS `message.send`), since the point of these tests is
`sync.request`'s own query correctness, not delivery — matching how
tests/test_message_routes.py's `GET /messages` tests seed history the same
way.

Written before app/ws.py's sync.request handling exists, following the
established tests-first precedent: collecting this file succeeds, but
every test here is expected to fail until Step 2's implementation lands —
today, `_process_frame` rejects any non-`message.send` frame type
(including `sync.request`) as "unsupported frame type from a client."
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import add_member, create_circle, create_message


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _sync_frame(*, target_type, target_id, since_id=None, limit=None):
    data = {"target_type": target_type, "target_id": target_id}
    if since_id is not None:
        data["since_id"] = since_id
    if limit is not None:
        data["limit"] = limit
    return {"type": "sync.request", "data": data}


def test_sync_request_dm_returns_history_since_cursor_respecting_limit_and_has_more(
    client, db_session, ws_login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(5)
    ]
    # Sort by id, not creation-loop order: UUIDv7 only guarantees ordering
    # across distinct milliseconds — a tight loop can generate several ids
    # inside the same millisecond, where tie-breaking is random-bit, not
    # loop order (same reasoning tests/test_repository.py's
    # get_messages_since tests already document).
    ordered = sorted(messages, key=lambda m: m.id)

    # Soft-delete one message in the middle — must never come back in a
    # sync.batch (docs/SCHEMA_DRAFT.md design question #2: "every
    # list/sync query adds AND deleted_at IS NULL").
    deleted = ordered[2]
    deleted.deleted_at = datetime.now(UTC)
    db_session.commit()

    alice_token = ws_login_as(alice)

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(
            _sync_frame(
                target_type="user", target_id=str(bob.id), since_id=str(ordered[0].id), limit=2
            )
        )
        batch = alice_ws.receive_json()

    assert batch["type"] == "sync.batch"
    body = batch["data"]
    assert body["target_type"] == "user"
    assert body["target_id"] == str(bob.id)
    # since_id excludes message 0 (cursor is exclusive); the soft-deleted
    # message 2 is excluded entirely; limit=2 caps the remaining 3
    # (messages 1, 3, 4) down to the first 2, in id order.
    assert [m["id"] for m in body["messages"]] == [str(ordered[1].id), str(ordered[3].id)]
    assert body["has_more"] is True


def test_sync_request_circle_returns_history_since_cursor(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id)

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="circle",
            target_circle_id=circle.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(3)
    ]
    ordered = sorted(messages, key=lambda m: m.id)
    db_session.commit()

    bob_token = ws_login_as(bob)

    with client.websocket_connect(f"/ws?token={bob_token}") as bob_ws:
        bob_ws.send_json(_sync_frame(target_type="circle", target_id=str(circle.id)))
        batch = bob_ws.receive_json()

    assert batch["type"] == "sync.batch"
    body = batch["data"]
    assert body["target_type"] == "circle"
    assert body["target_id"] == str(circle.id)
    assert [m["id"] for m in body["messages"]] == [str(m.id) for m in ordered]
    assert body["has_more"] is False


def test_sync_request_circle_requires_membership(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    stranger = _make_db_user(db_session, "Stranger")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    create_message(
        db_session,
        author_id=alice.id,
        target_type="circle",
        target_circle_id=circle.id,
        kind="text",
        text="circle chat",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()

    stranger_token = ws_login_as(stranger)

    with client.websocket_connect(f"/ws?token={stranger_token}") as stranger_ws:
        stranger_ws.send_json(_sync_frame(target_type="circle", target_id=str(circle.id)))
        response = stranger_ws.receive_json()

    assert response["type"] == "error"
    assert response["data"]["code"] == "UNAUTHORIZED"

    # No DM-equivalent "conversation the caller isn't part of" case exists
    # to test here: the resolved conversation for target_type=user is
    # always (caller, target_id), so a caller can never address anyone
    # else's DM through this wire shape at all — same point
    # tests/test_message_routes.py::test_get_messages_rejects_non_participant's
    # docstring already makes for GET /messages, which sync.request mirrors
    # exactly (Path 1).


def test_sync_request_stale_cursor_returns_full_history_no_crash(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(3)
    ]
    ordered = sorted(messages, key=lambda m: m.id)
    db_session.commit()

    alice_token = ws_login_as(alice)
    # The all-zero UUID: no retention/pruning exists yet, so "stale" just
    # means "older than anything real" — every real UUIDv7 id sorts after
    # it (a nonzero millisecond timestamp occupies its high bits for any
    # real date), so `id > cursor` handles this correctly with no special
    # casing (Step 0 answer D), whether or not this specific value is a
    # "valid" UUIDv7 shape — Postgres compares raw UUID bytes either way.
    stale_cursor = str(uuid.UUID(int=0))

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(
            _sync_frame(target_type="user", target_id=str(bob.id), since_id=stale_cursor)
        )
        batch = alice_ws.receive_json()

    assert batch["type"] == "sync.batch"
    body = batch["data"]
    assert [m["id"] for m in body["messages"]] == [str(m.id) for m in ordered]
    assert body["has_more"] is False


def test_sync_request_absent_cursor_returns_full_history_from_beginning(
    client, db_session, ws_login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(3)
    ]
    ordered = sorted(messages, key=lambda m: m.id)
    db_session.commit()

    bob_token = ws_login_as(bob)

    with client.websocket_connect(f"/ws?token={bob_token}") as bob_ws:
        # since_id omitted entirely — SyncRequest.since_id defaults to
        # None, matching a first-ever sync from a brand new client.
        bob_ws.send_json(_sync_frame(target_type="user", target_id=str(alice.id)))
        batch = bob_ws.receive_json()

    assert batch["type"] == "sync.batch"
    body = batch["data"]
    assert [m["id"] for m in body["messages"]] == [str(m.id) for m in ordered]
    assert body["has_more"] is False


def test_sync_request_same_cursor_twice_is_idempotent(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(3)
    ]
    db_session.commit()

    alice_token = ws_login_as(alice)
    request_frame = _sync_frame(target_type="user", target_id=str(bob.id))

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        alice_ws.send_json(request_frame)
        first = alice_ws.receive_json()
        alice_ws.send_json(request_frame)
        second = alice_ws.receive_json()

    # Checked before the equality comparison, deliberately: two identical
    # *rejections* (e.g. today's "unsupported frame type" error, before
    # this phase is implemented) would also be equal to each other and
    # would trivially pass `first == second` without proving anything
    # about real idempotency. Asserting the actual batch shape first means
    # this test can't pass by accident against an unimplemented handler.
    assert first["type"] == "sync.batch"
    assert len(first["data"]["messages"]) == len(messages)

    # A read, not a mutation: asking the identical question twice gets the
    # identical answer both times.
    assert first == second

    rows = db_session.execute(select(Message).where(Message.author_id == alice.id)).scalars().all()
    assert len(rows) == len(messages)


def test_sync_request_two_connections_same_user_get_independent_correct_results(
    client, db_session, ws_login_as
):
    # Read-only, so this is a sanity check rather than the forced-
    # interleaving races Phase 5's write path needed (Step 0 answer,
    # confirmed): two connections issuing sync.request on the same
    # conversation must each get the correct batch back, with nothing
    # shared or corrupted between them (e.g. one connection's query
    # accidentally observing or altering state meant for the other's).
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    messages = [
        create_message(
            db_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text=f"message {i}",
            client_msg_id=uuid.uuid4(),
        )
        for i in range(4)
    ]
    ordered = sorted(messages, key=lambda m: m.id)
    db_session.commit()

    alice_token = ws_login_as(alice)
    request_frame = _sync_frame(target_type="user", target_id=str(bob.id))

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_device_1,
        client.websocket_connect(f"/ws?token={alice_token}") as alice_device_2,
    ):
        alice_device_1.send_json(request_frame)
        alice_device_2.send_json(request_frame)
        batch_1 = alice_device_1.receive_json()
        batch_2 = alice_device_2.receive_json()

    assert batch_1 == batch_2
    assert [m["id"] for m in batch_1["data"]["messages"]] == [str(m.id) for m in ordered]
