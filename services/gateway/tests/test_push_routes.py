"""HTTP route tests for POST /push/subscribe and POST /push/unsubscribe —
exercised against the real app.main.app via TestClient, with the get_db
dependency overridden to the test Postgres session and get_current_user
swapped per test via login_as (tests/conftest.py) — same conventions as
tests/test_message_routes.py and tests/test_circle_routes.py.

Gateway-local Pydantic models, not contracts/chat/ (Step 0's confirmed
answer to design question C): there's no second consumer of this shape
yet, so it isn't promoted into the shared contract package.

Written before app/push.py exists — collecting this file succeeds
(nothing here imports it directly), but every test is expected to fail
with a 404 (no such route registered on app.main.app) until Step 2's
implementation lands.
"""

from sqlalchemy import select

from app.db.models import PushSubscription
from app.db.models import User as DbUser
from app.db.repository import upsert_push_subscription


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def test_post_push_subscribe_requires_auth(client, db_session):
    response = client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        },
    )

    assert response.status_code == 401


def test_post_push_subscribe_creates_row_tied_to_callers_own_user_id(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    response = client.post(
        "/push/subscribe",
        json={
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        },
    )

    assert response.status_code == 200

    rows = (
        db_session.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == "https://fcm.googleapis.com/fcm/send/abc123"
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == alice.id
    assert rows[0].p256dh == "p256dh-value"
    assert rows[0].auth == "auth-value"


def test_post_push_subscribe_updates_keys_in_place_on_repeat_call(client, db_session, login_as):
    # Mirrors the repository-level upsert test
    # (test_upsert_push_subscription_updates_keys_on_existing_endpoint):
    # a browser rotating its own keys for the same endpoint must update
    # the existing row, not create a second one or error.
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)
    endpoint = "https://fcm.googleapis.com/fcm/send/abc123"

    first = client.post(
        "/push/subscribe",
        json={"endpoint": endpoint, "keys": {"p256dh": "old-p256dh", "auth": "old-auth"}},
    )
    second = client.post(
        "/push/subscribe",
        json={"endpoint": endpoint, "keys": {"p256dh": "new-p256dh", "auth": "new-auth"}},
    )

    assert first.status_code == 200
    assert second.status_code == 200

    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].p256dh == "new-p256dh"
    assert rows[0].auth == "new-auth"


def test_post_push_unsubscribe_removes_callers_own_subscription(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    endpoint = "https://fcm.googleapis.com/fcm/send/abc123"
    upsert_push_subscription(db_session, user_id=alice.id, endpoint=endpoint, p256dh="p", auth="a")
    db_session.commit()
    login_as(alice)

    response = client.post("/push/unsubscribe", json={"endpoint": endpoint})

    assert response.status_code == 200
    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
        .scalars()
        .all()
    )
    assert rows == []


def test_post_push_unsubscribe_missing_endpoint_is_a_noop(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    response = client.post(
        "/push/unsubscribe", json={"endpoint": "https://never-subscribed.example/x"}
    )

    assert response.status_code == 200


def test_post_push_unsubscribe_does_not_remove_another_users_subscription(
    client, db_session, login_as
):
    # Proves the *route* enforces ownership scoping end to end, not just
    # delete_push_subscription in isolation (already proven at the
    # repository layer by
    # test_delete_push_subscription_wrong_user_id_is_a_noop) — a caller
    # can't unsubscribe someone else's device by guessing or replaying
    # their endpoint.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    endpoint = "https://fcm.googleapis.com/fcm/send/bobs-endpoint"
    upsert_push_subscription(db_session, user_id=bob.id, endpoint=endpoint, p256dh="p", auth="a")
    db_session.commit()
    login_as(alice)

    response = client.post("/push/unsubscribe", json={"endpoint": endpoint})

    assert response.status_code == 200
    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == bob.id
