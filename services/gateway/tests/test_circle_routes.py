"""HTTP route tests for GET/POST /circles and POST /circles/{id}/members —
same TestClient + get_db/get_current_user override pattern as
tests/test_message_routes.py (see tests/conftest.py's client/login_as
fixtures). Requests/responses are checked against contracts/chat/circles.py's
actual wire shapes (CircleCreate/Circle, MembershipCreate/Membership).

Written before app/circles.py exists — every test here is expected to fail
with a 404 (no such route registered on app.main.app) until Step 2's
implementation lands.
"""

import uuid

from app.db.models import Membership
from app.db.models import User as DbUser
from app.db.repository import add_member, create_circle


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def test_get_circles_returns_only_callers_circles(client, db_session, login_as):
    # The exact mock-server bug (list(_circles.values()) returning every
    # circle unconditionally, docs/SCHEMA_DRAFT.md's memberships-index
    # section flags it by name) that must not survive into the real
    # gateway: 4 circles seeded, Alice is a member of 2 of them.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")

    circle_1 = create_circle(db_session, name="Circle One", created_by=alice.id)
    circle_2 = create_circle(db_session, name="Circle Two", created_by=alice.id)
    circle_3 = create_circle(db_session, name="Circle Three", created_by=bob.id)
    circle_4 = create_circle(db_session, name="Circle Four", created_by=bob.id)

    add_member(db_session, circle_id=circle_1.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle_2.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle_3.id, user_id=bob.id, role="admin")
    add_member(db_session, circle_id=circle_4.id, user_id=bob.id, role="admin")

    login_as(alice)
    response = client.get("/circles")

    assert response.status_code == 200
    body = response.json()
    returned_ids = {c["id"] for c in body}
    assert returned_ids == {str(circle_1.id), str(circle_2.id)}

    # Field-level, not just membership in the right id set: each returned
    # circle must carry its own real name/created_by, not a mismatched or
    # blank value from a join gone wrong (e.g. the list comprehension
    # pairing the wrong Circle row with the wrong Membership row).
    by_id = {c["id"]: c for c in body}
    assert by_id[str(circle_1.id)]["name"] == "Circle One"
    assert by_id[str(circle_1.id)]["created_by"] == str(alice.id)
    assert by_id[str(circle_2.id)]["name"] == "Circle Two"
    assert by_id[str(circle_2.id)]["created_by"] == str(alice.id)


def test_post_circles_creates_circle(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    response = client.post("/circles", json={"name": "Evening Satsang"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Evening Satsang"
    # created_by comes from the authenticated caller, never a request
    # body field — contracts/chat/README.md's POST /circles section.
    assert body["created_by"] == str(alice.id)
    assert isinstance(body["id"], str) and body["id"]
    assert "created_at" in body

    # The creator ends up an admin member of the circle they just
    # created — confirmed decision (no doc mandates this; the reference
    # mock does the opposite and auto-adds nobody), role="admin".
    membership = db_session.get(Membership, (uuid.UUID(body["id"]), alice.id))
    assert membership is not None
    assert membership.role == "admin"


def test_post_circles_members_adds_member(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(alice)

    response = client.post(f"/circles/{circle.id}/members", json={"user_id": str(bob.id)})

    assert response.status_code == 200
    body = response.json()
    assert body["circle_id"] == str(circle.id)
    assert body["user_id"] == str(bob.id)
    assert body["role"] == "member"

    membership = db_session.get(Membership, (circle.id, bob.id))
    assert membership is not None


def test_post_circles_members_requires_authorization(client, db_session, login_as):
    # A stranger cannot add people to a circle they don't belong to —
    # asserting 403 for a caller with no membership row on that circle at
    # all, not merely the wrong role.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    stranger = _make_db_user(db_session, "Stranger")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(stranger)

    response = client.post(f"/circles/{circle.id}/members", json={"user_id": str(bob.id)})

    assert response.status_code == 403

    membership = db_session.get(Membership, (circle.id, bob.id))
    assert membership is None
