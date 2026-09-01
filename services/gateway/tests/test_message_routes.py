"""HTTP route tests for POST /messages and GET /messages — exercised
against the real app.main.app via TestClient, with the get_db dependency
overridden to the test Postgres session and get_current_user swapped per
test via login_as (tests/conftest.py). Requests/responses are checked
against contracts/chat/'s actual wire shapes (MessageIn request,
AckOut/SyncBatch response) rather than an invented shape.

Written before app/messages.py exists — collecting this file succeeds
(nothing here imports it directly), but every test is expected to fail
with a 404 (no such route registered on app.main.app) until Step 2's
implementation lands. That failure is the point: these tests fix the
route contract the implementation has to satisfy, not the other way
around.
"""

import uuid

from sqlalchemy import select

from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import add_member, create_circle, create_message


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def test_post_messages_to_circle_creates_message_and_returns_expected_shape(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(alice)

    client_msg_id = str(uuid.uuid4())
    response = client.post(
        "/messages",
        json={
            "client_msg_id": client_msg_id,
            "target_type": "circle",
            "target_id": str(circle.id),
            "kind": "text",
            "text": "hello circle",
        },
    )

    assert response.status_code == 200
    body = response.json()
    # AckOut's exact field set — contracts/chat/messages.py::AckOut —
    # nothing invented, nothing extra assumed.
    assert set(body.keys()) >= {"client_msg_id", "id", "status"}
    assert body["client_msg_id"] == client_msg_id
    assert isinstance(body["id"], str) and body["id"]
    assert body["status"] in ("pending", "delivered", "held", "blocked", "failed")


def test_post_messages_dm_creates_message(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    client_msg_id = str(uuid.uuid4())
    response = client.post(
        "/messages",
        json={
            "client_msg_id": client_msg_id,
            "target_type": "user",
            "target_id": str(bob.id),
            "kind": "text",
            "text": "hi Bob",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["client_msg_id"] == client_msg_id
    assert isinstance(body["id"], str) and body["id"]


def test_post_messages_idempotent_on_client_msg_id(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    login_as(alice)

    client_msg_id = str(uuid.uuid4())
    body = {
        "client_msg_id": client_msg_id,
        "target_type": "user",
        "target_id": str(bob.id),
        "kind": "text",
        "text": "first try",
    }

    first = client.post("/messages", json=body)
    second = client.post("/messages", json={**body, "text": "retried send after dropped ack"})

    assert first.status_code == 200
    assert second.status_code == 200
    # Same server-assigned id both times — the repository's idempotency
    # guarantee (uq_messages_author_client_msg_id) actually reaching the
    # HTTP layer, not just the repository's own unit tests.
    assert first.json()["id"] == second.json()["id"]

    rows = (
        db_session.execute(
            select(Message).where(
                Message.author_id == alice.id,
                Message.client_msg_id == uuid.UUID(client_msg_id),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_post_messages_requires_auth(client, db_session):
    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": str(uuid.uuid4()),
            "kind": "text",
            "text": "hi",
        },
    )

    assert response.status_code == 401


def test_post_messages_rejects_invalid_target_type(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "not-a-real-type",
            "target_id": str(uuid.uuid4()),
            "kind": "text",
            "text": "hi",
        },
    )

    # Pydantic's own TargetType enum validation on MessageIn rejects this
    # at the contract boundary — a 422, not a 500 reaching the route body
    # or the database at all.
    assert response.status_code == 422


def test_post_messages_to_circle_requires_membership(client, db_session, login_as):
    # A stranger posting into a circle they don't belong to — the
    # circle-member authorization check STEP 2 item 3 requires, even
    # though not itself one of the originally enumerated test names.
    alice = _make_db_user(db_session, "Alice")
    stranger = _make_db_user(db_session, "Stranger")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(stranger)

    response = client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": str(circle.id),
            "kind": "text",
            "text": "sneaking in",
        },
    )

    assert response.status_code == 403


def test_get_messages_since_resolves_target_to_conversation_and_returns_history(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    # Both directions of the same DM. The point of this test isn't
    # create_message's own conversation resolution (already proven end to
    # end in test_repository.py) — it's that the HTTP route resolves
    # target_type=user/target_id=<the other party> to the *same*
    # conversation server-side and returns both messages, with the client
    # never sending conversation_id itself (docs/SCHEMA_DRAFT.md design
    # question #1a's "no wire contract change" point, now proven through
    # the actual route).
    first = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="hi Bob",
        client_msg_id=uuid.uuid4(),
    )
    second = create_message(
        db_session,
        author_id=bob.id,
        target_type="user",
        target_user_id=alice.id,
        kind="text",
        text="hi Alice",
        client_msg_id=uuid.uuid4(),
    )

    login_as(alice)
    response = client.get("/messages", params={"target_type": "user", "target_id": str(bob.id)})

    assert response.status_code == 200
    body = response.json()
    assert body["target_type"] == "user"
    assert body["target_id"] == str(bob.id)
    returned_ids = {m["id"] for m in body["messages"]}
    assert returned_ids == {str(first.id), str(second.id)}
    assert body["has_more"] is False

    # Per-message target_id must reflect each row's own real recipient
    # (mirroring the MessageIn.target_id it was created with), not just
    # echo the sync query's target_id onto every row in the batch — the
    # two directions of this DM have different real targets (first was
    # addressed to Bob, second to Alice), and MessageOut carries its own
    # target_type/target_id specifically so it's meaningful standalone
    # (e.g. a `message.new` WS frame outside any batch context).
    by_id = {m["id"]: m for m in body["messages"]}
    assert by_id[str(first.id)]["target_id"] == str(bob.id)
    assert by_id[str(second.id)]["target_id"] == str(alice.id)


def test_get_messages_rejects_non_participant(client, db_session, login_as):
    # Circle case: a caller who isn't a member gets 403, not the circle's
    # messages. (The DM case has no equivalent scenario to test — the
    # resolved conversation is always (caller, target_id), so a caller can
    # never address anyone else's DM through this wire shape; there's no
    # "non-participant" state to reach for target_type=user.)
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

    login_as(stranger)
    response = client.get(
        "/messages", params={"target_type": "circle", "target_id": str(circle.id)}
    )

    assert response.status_code == 403
