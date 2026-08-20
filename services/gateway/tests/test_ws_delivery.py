"""WS delivery tests for app/ws.py's real `message.send` -> persist ->
fan-out path (Week 3 Phase 5) — replacing the Week 1 echo stub.

Uses `TestClient.websocket_connect` against two or more simultaneously-open
connections (real multi-socket scenarios, not one socket talking to
itself), the same `get_db` override tests/conftest.py's `client` fixture
already provides for HTTP route tests, plus the WS-specific `ws_login_as`
fixture (app/ws.py's `/ws` route authenticates via `app.auth.user_from_token`
directly, not FastAPI's `Depends`/`dependency_overrides`, so HTTP's
`login_as` doesn't reach it — see conftest.py for why).

Written before app/ws.py's real delivery logic exists, following the same
tests-first precedent as tests/test_message_routes.py and
tests/test_repository.py: collecting this file succeeds (ConnectionManager
already exists, just not `send_to_user`/`broadcast` yet), but every test
here is expected to fail until Step 2's implementation lands.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import add_member, create_circle
from app.ws import ConnectionManager


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


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


def test_dm_message_send_persists_and_delivers_to_recipient(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    client_msg_id = str(uuid.uuid4())

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=client_msg_id,
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )

        ack = alice_ws.receive_json()
        assert ack["type"] == "message.ack"
        assert ack["data"]["client_msg_id"] == client_msg_id
        assert isinstance(ack["data"]["id"], str) and ack["data"]["id"]
        assert ack["data"]["status"] in ("pending", "delivered", "held", "blocked", "failed")

        new = bob_ws.receive_json()
        assert new["type"] == "message.new"
        assert new["data"]["id"] == ack["data"]["id"]
        assert new["data"]["author_id"] == str(alice.id)
        assert new["data"]["target_type"] == "user"
        assert new["data"]["target_id"] == str(bob.id)
        assert new["data"]["text"] == "hi Bob"

    # Independently query the database — proving persistence, not just
    # that something arrived over the wire.
    row = db_session.execute(
        select(Message).where(Message.id == uuid.UUID(ack["data"]["id"]))
    ).scalar_one()
    assert row.author_id == alice.id
    assert row.target_user_id == bob.id
    assert row.text == "hi Bob"


def test_message_send_failure_does_not_fan_out(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    # A syntactically valid UUID with no `users` row behind it — fails at
    # the repository layer (target_user_id's FK), not at MessageIn's own
    # validation, so this exercises create_message's failure path
    # specifically, not a contract-shape rejection.
    nonexistent_target = str(uuid.uuid4())
    failed_client_msg_id = str(uuid.uuid4())

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=failed_client_msg_id,
                target_type="user",
                target_id=nonexistent_target,
                text="this should fail",
            )
        )
        response = alice_ws.receive_json()
        assert response["type"] == "error"

        # Nothing leaked to Bob — a real, connected, unrelated party —
        # from the failed send. Proven by sending a distinguishable
        # sentinel next and confirming it's the very first frame Bob's
        # socket receives (WS is an ordered stream per connection, so if
        # anything from the failed send had been queued to Bob, it would
        # arrive before the sentinel).
        sentinel_id = str(uuid.uuid4())
        alice_ws.send_json(
            _send_frame(
                client_msg_id=sentinel_id,
                target_type="user",
                target_id=str(bob.id),
                text="sentinel",
            )
        )
        alice_ws.receive_json()  # sentinel's ack
        sentinel_new = bob_ws.receive_json()
        assert sentinel_new["type"] == "message.new"
        assert sentinel_new["data"]["text"] == "sentinel"

    # Nothing persisted for the failed send.
    rows = (
        db_session.execute(
            select(Message).where(Message.client_msg_id == uuid.UUID(failed_client_msg_id))
        )
        .scalars()
        .all()
    )
    assert rows == []


def test_message_send_invalid_payload_returns_error_frame_not_close(
    client, db_session, ws_login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)

    with client.websocket_connect(f"/ws?token={alice_token}") as alice_ws:
        # Missing client_msg_id and kind — fails MessageIn validation.
        alice_ws.send_json(
            {"type": "message.send", "data": {"target_type": "user", "target_id": str(bob.id)}}
        )
        error = alice_ws.receive_json()
        assert error["type"] == "error"
        assert error["data"]["code"] == "VALIDATION_FAILED"

        # The connection stays open and usable — not a close, which would
        # force a reconnect for a problem that isn't a connection problem.
        # A subsequent valid send gets a real ack on the same socket.
        client_msg_id = str(uuid.uuid4())
        alice_ws.send_json(
            _send_frame(
                client_msg_id=client_msg_id,
                target_type="user",
                target_id=str(bob.id),
                text="still works",
            )
        )
        ack = alice_ws.receive_json()
        assert ack["type"] == "message.ack"
        assert ack["data"]["client_msg_id"] == client_msg_id


def test_message_send_idempotent_client_msg_id_does_not_double_deliver(
    client, db_session, ws_login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    shared_client_msg_id = str(uuid.uuid4())

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=shared_client_msg_id,
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob",
            )
        )
        first_ack = alice_ws.receive_json()
        assert first_ack["type"] == "message.ack"

        # Same client_msg_id again — a client retrying after a dropped ack
        # on an unreliable network, the exact scenario this key exists for.
        alice_ws.send_json(
            _send_frame(
                client_msg_id=shared_client_msg_id,
                target_type="user",
                target_id=str(bob.id),
                text="retried send after dropped ack",
            )
        )
        second_ack = alice_ws.receive_json()
        assert second_ack["type"] == "message.ack"
        # Same server-assigned id both times — the repository's existing
        # idempotency guarantee, now reaching the WS layer too.
        assert second_ack["data"]["id"] == first_ack["data"]["id"]

        # Bob gets exactly one message.new, not two. Proven the same way
        # as the failure test: a distinguishable sentinel sent next must
        # be the very next thing Bob's socket sees.
        first_new = bob_ws.receive_json()
        assert first_new["type"] == "message.new"
        assert first_new["data"]["id"] == first_ack["data"]["id"]

        sentinel_id = str(uuid.uuid4())
        alice_ws.send_json(
            _send_frame(
                client_msg_id=sentinel_id,
                target_type="user",
                target_id=str(bob.id),
                text="sentinel",
            )
        )
        alice_ws.receive_json()  # sentinel's ack
        sentinel_new = bob_ws.receive_json()
        assert sentinel_new["data"]["text"] == "sentinel"

    rows = (
        db_session.execute(
            select(Message).where(
                Message.author_id == alice.id, Message.client_msg_id == shared_client_msg_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_message_send_concurrent_retry_fans_out_message_new_exactly_once(
    client, db_session, engine, ws_login_as
):
    """A purely sequential retry on one connection (the test above) can't
    tell a correct atomic implementation apart from a buggy
    read-then-act one — it never creates the race window a real
    near-simultaneous retry (e.g. a dropped ack causing a second WS
    connection to resend while the first is still in flight) would. This
    forces that interleaving directly, same technique as Phase 3's
    test_get_or_create_conversation_recovers_from_concurrent_insert_race
    and test_repository.py's own
    test_create_message_with_created_flag_exactly_one_winner_under_concurrent_race:
    patch `db_session.add` (the same session Alice's WS connection uses,
    via the `get_db` override) to inject a genuinely concurrent write for
    the same (author_id, client_msg_id) at the exact moment Alice's own
    WS send reaches the database, rather than hoping two sequential sends
    happen to race — or, as a first attempt at this test found, rather
    than running a "racer" to full completion *before* calling the real
    handler, which is sequential in disguise and never opens the race
    window either.
    """
    from app.db.repository import create_message_with_created_flag as real_create_with_flag

    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    # Establish the DM's conversation row up front (see the equivalent
    # comment in test_repository.py's own race test): otherwise the
    # *first* db_session.add() this send performs is the conversation
    # row, not the message row, misaligning the sabotage below with the
    # race window this test means to force around the message insert
    # specifically.
    real_create_with_flag(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="prior message establishing the conversation",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()  # visible to the racer's separate session below
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    shared_client_msg_id = uuid.uuid4()
    racer_session = Session(engine)
    racer_result: dict = {}
    original_add = db_session.add

    def add_after_concurrent_racer_commits(instance, *args, **kwargs):
        # Fires when Alice's own WS send — running on the shared
        # db_session via the get_db override — is about to insert its own
        # Message row (the conversation already exists, so this is that
        # insert's session.add(), not the conversation's). A wholly
        # separate session/connection, standing in for a second, genuinely
        # concurrent WS send of the identical client_msg_id, completes its
        # own write and commits right here, between Alice's row being
        # constructed and flushed.
        db_session.add = original_add
        racer_message, racer_created = real_create_with_flag(
            racer_session,
            author_id=alice.id,
            target_type="user",
            target_user_id=bob.id,
            kind="text",
            text="racer",
            client_msg_id=shared_client_msg_id,
        )
        racer_session.commit()
        racer_result["id"] = racer_message.id
        racer_result["created"] = racer_created
        return original_add(instance, *args, **kwargs)

    db_session.add = add_after_concurrent_racer_commits
    try:
        with (
            client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
            client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
        ):
            alice_ws.send_json(
                _send_frame(
                    client_msg_id=str(shared_client_msg_id),
                    target_type="user",
                    target_id=str(bob.id),
                    text="hi Bob",
                )
            )
            ack = alice_ws.receive_json()
            assert ack["type"] == "message.ack"
            # Lost the race — same server id as the racer's, proving it
            # recovered the racer's row rather than inserting its own.
            assert ack["data"]["id"] == str(racer_result["id"])

            # Nothing reaches Bob from Alice's (losing) call. Proven the
            # same sentinel way as the other fan-out tests: the very next
            # distinguishable frame Bob's socket sees is unrelated to
            # this send.
            sentinel_id = str(uuid.uuid4())
            alice_ws.send_json(
                _send_frame(
                    client_msg_id=sentinel_id,
                    target_type="user",
                    target_id=str(bob.id),
                    text="sentinel",
                )
            )
            alice_ws.receive_json()  # sentinel ack
            sentinel_new = bob_ws.receive_json()
            assert sentinel_new["data"]["text"] == "sentinel"
    finally:
        db_session.add = original_add
        racer_session.close()

    # The racer — standing in for the concurrent send that genuinely won —
    # correctly observed created=True. Combined with Alice's own call
    # observing a loss (recovered id) above, exactly one side of this
    # race fans out message.new: never zero, never two.
    assert racer_result["created"] is True

    rows = (
        db_session.execute(
            select(Message).where(
                Message.author_id == alice.id, Message.client_msg_id == shared_client_msg_id
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_dm_message_send_echoes_to_senders_own_other_devices(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={alice_token}") as alice_second_device,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob, from my phone",
            )
        )
        ack = alice_ws.receive_json()
        assert ack["type"] == "message.ack"

        bob_new = bob_ws.receive_json()
        assert bob_new["type"] == "message.new"
        assert bob_new["data"]["id"] == ack["data"]["id"]

        # Confirmed product decision (Step 0), now applied consistently to
        # DMs as well as circles: the sender's OTHER connected device also
        # gets message.new for their own sent DM, without waiting for a
        # sync.
        alice_second_new = alice_second_device.receive_json()
        assert alice_second_new["type"] == "message.new"
        assert alice_second_new["data"]["id"] == ack["data"]["id"]

        # But not a duplicate on the exact socket that sent it — proven
        # via the same sentinel technique as the circle fan-out test.
        sentinel_id = str(uuid.uuid4())
        alice_ws.send_json(
            _send_frame(
                client_msg_id=sentinel_id,
                target_type="user",
                target_id=str(bob.id),
                text="sentinel",
            )
        )
        next_on_alice_ws = alice_ws.receive_json()
        assert next_on_alice_ws["type"] == "message.ack"
        assert next_on_alice_ws["data"]["client_msg_id"] == sentinel_id


def test_dm_message_send_to_self_is_rejected(client, db_session, ws_login_as):
    # Not a supported case: docs/SCHEMA_DRAFT.md's `conversations` table
    # (design question #1a) enforces a *strict* CHECK (user_a < user_b),
    # making a self-pair structurally impossible at the DB level, and it's
    # never discussed anywhere as a "note to self" feature. Deterministic
    # list-construction concern, not a race: with two connected devices,
    # an unvalidated self-DM's fan-out (`recipients = [target_user_id,
    # caller_id]`, both the same id here) would push the same message.new
    # to the sender's second device twice, one per duplicate list entry —
    # this proves that never happens, because the send is rejected before
    # any recipients list is ever built.
    alice = _make_db_user(db_session, "Alice")
    alice_token = ws_login_as(alice)
    rejected_client_msg_id = str(uuid.uuid4())

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={alice_token}") as alice_second_device,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=rejected_client_msg_id,
                target_type="user",
                target_id=str(alice.id),
                text="note to self",
            )
        )
        error = alice_ws.receive_json()
        assert error["type"] == "error"
        assert error["data"]["code"] == "VALIDATION_FAILED"

        # Nothing reached the second device either — not once, not twice.
        # Proven the same sentinel way as the other fan-out tests: a
        # distinguishable follow-up DM to a real other party must be the
        # very first thing the second device sees.
        bob = _make_db_user(db_session, "Bob")
        sentinel_id = str(uuid.uuid4())
        alice_ws.send_json(
            _send_frame(
                client_msg_id=sentinel_id,
                target_type="user",
                target_id=str(bob.id),
                text="sentinel",
            )
        )
        alice_ws.receive_json()  # sentinel ack
        sentinel_new = alice_second_device.receive_json()
        assert sentinel_new["type"] == "message.new"
        assert sentinel_new["data"]["text"] == "sentinel"

    rows = (
        db_session.execute(
            select(Message).where(
                Message.author_id == alice.id,
                Message.client_msg_id == uuid.UUID(rejected_client_msg_id),
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


def test_message_send_to_circle_requires_membership(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    stranger = _make_db_user(db_session, "Stranger")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    stranger_token = ws_login_as(stranger)

    with client.websocket_connect(f"/ws?token={stranger_token}") as stranger_ws:
        stranger_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="circle",
                target_id=str(circle.id),
                text="sneaking in",
            )
        )
        error = stranger_ws.receive_json()
        assert error["type"] == "error"
        assert error["data"]["code"] == "UNAUTHORIZED"

    rows = (
        db_session.execute(select(Message).where(Message.target_circle_id == circle.id))
        .scalars()
        .all()
    )
    assert rows == []


def test_circle_message_fans_out_to_all_connected_members(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    carol = _make_db_user(db_session, "Carol")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id)
    add_member(db_session, circle_id=circle.id, user_id=carol.id)
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)
    # Carol is a member but never connects — a WS delivery test can't prove
    # anything about her; she only ever picks this up via a later
    # GET /messages sync, which tests/test_message_routes.py already covers.

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_second_device,
    ):
        bob_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="circle",
                target_id=str(circle.id),
                text="evening satsang starting",
            )
        )

        ack = bob_ws.receive_json()
        assert ack["type"] == "message.ack"

        alice_new = alice_ws.receive_json()
        assert alice_new["type"] == "message.new"
        assert alice_new["data"]["target_type"] == "circle"
        assert alice_new["data"]["target_id"] == str(circle.id)
        assert alice_new["data"]["id"] == ack["data"]["id"]

        # Confirmed product decision (Step 0): a circle poster's own OTHER
        # connected devices also get message.new, for multi-device sync.
        bob_second_new = bob_second_device.receive_json()
        assert bob_second_new["type"] == "message.new"
        assert bob_second_new["data"]["id"] == ack["data"]["id"]

        # But not a duplicate on the exact socket that sent it — that
        # socket already has the ack. Proven via the same sentinel
        # technique: nothing extra is queued there before the next frame.
        sentinel_id = str(uuid.uuid4())
        bob_ws.send_json(
            _send_frame(
                client_msg_id=sentinel_id,
                target_type="user",
                target_id=str(alice.id),
                text="sentinel",
            )
        )
        next_on_bob_ws = bob_ws.receive_json()
        assert next_on_bob_ws["type"] == "message.ack"
        assert next_on_bob_ws["data"]["client_msg_id"] == sentinel_id


def test_send_to_user_reaches_all_of_that_users_connected_devices(client, db_session, ws_login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    alice_token = ws_login_as(alice)
    bob_token = ws_login_as(bob)

    with (
        client.websocket_connect(f"/ws?token={alice_token}") as alice_ws,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_device_1,
        client.websocket_connect(f"/ws?token={bob_token}") as bob_device_2,
    ):
        alice_ws.send_json(
            _send_frame(
                client_msg_id=str(uuid.uuid4()),
                target_type="user",
                target_id=str(bob.id),
                text="hi Bob, on both devices",
            )
        )
        ack = alice_ws.receive_json()
        assert ack["type"] == "message.ack"

        new_on_device_1 = bob_device_1.receive_json()
        new_on_device_2 = bob_device_2.receive_json()
        assert new_on_device_1["type"] == "message.new"
        assert new_on_device_2["type"] == "message.new"
        assert new_on_device_1["data"]["id"] == ack["data"]["id"]
        assert new_on_device_2["data"]["id"] == ack["data"]["id"]


class _FakeWebSocket:
    """Stand-in for a Starlette WebSocket, for testing ConnectionManager's
    fan-out concurrency directly — no ASGI transport, no event loop
    indirection, so the exact interleaving below is deterministic instead
    of hoping real concurrency reproduces it."""

    def __init__(self):
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


async def test_broadcast_survives_concurrent_disconnect():
    # Same sabotage-then-restore verification technique as Phase 3's
    # test_get_or_create_conversation_recovers_from_concurrent_insert_race:
    # deterministically inject the race at the exact moment that matters,
    # rather than hoping real concurrency reproduces it.
    manager = ConnectionManager()
    device_a, device_b, device_c = _FakeWebSocket(), _FakeWebSocket(), _FakeWebSocket()
    await manager.connect("alice", device_a)
    await manager.connect("alice", device_b)
    await manager.connect("alice", device_c)

    real_send_a = device_a.send_json

    async def sabotage_then_send(data):
        # Simulates device_b's own receive loop noticing its connection
        # died and calling manager.disconnect() for it, interleaved with
        # device_a's send still in flight — exactly what a single-threaded
        # event loop can produce when one of several sockets for the same
        # user drops mid-fan-out to another.
        manager.disconnect("alice", device_b)
        await real_send_a(data)

    device_a.send_json = sabotage_then_send

    async def raise_disconnected(data):
        # device_b's underlying connection is now actually dead — a real
        # send on it would raise, not silently no-op.
        raise RuntimeError("device_b's connection is already closed")

    device_b.send_json = raise_disconnected

    frame = {"type": "message.new", "data": {"id": "m1"}}
    await manager.send_to_user("alice", frame)  # must not raise

    assert device_a.sent == [frame]
    assert device_c.sent == [frame]
