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


def test_ordinary_member_can_still_add_an_ordinary_member(client, db_session, login_as):
    # The fix below (plain member cannot grant an elevated role) must not
    # regress the existing, intended "any member can invite" behaviour for
    # the default (member) role -- this is the case the privilege-
    # escalation fix must NOT break.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    carol = _make_db_user(db_session, "Carol")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    login_as(bob)

    response = client.post(f"/circles/{circle.id}/members", json={"user_id": str(carol.id)})

    assert response.status_code == 200
    membership = db_session.get(Membership, (circle.id, carol.id))
    assert membership is not None
    assert membership.role == "member"


def test_ordinary_member_cannot_grant_admin_to_a_new_member(client, db_session, login_as):
    # The actual vulnerability: MembershipCreate.role is fully
    # caller-controlled and the route previously checked only that the
    # caller was A member, any role, before honoring whatever role they
    # asked to grant someone else -- so a plain member could add a
    # second, colluding account directly as admin. No test exercised this
    # boundary before this one: every other test either grants the caller
    # admin first, or only ever requests the default (member) role.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    mallory = _make_db_user(db_session, "Mallory")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    login_as(bob)

    response = client.post(
        f"/circles/{circle.id}/members",
        json={"user_id": str(mallory.id), "role": "admin"},
    )

    assert response.status_code == 403
    # Not just the right status code -- confirm the escalation genuinely
    # did not happen, since a 403 returned AFTER a commit would be a much
    # worse bug than the endpoint just being slow to reject.
    membership = db_session.get(Membership, (circle.id, mallory.id))
    assert membership is None


def test_admin_can_grant_admin_to_a_new_member(client, db_session, login_as):
    # The fix's other side: an actual admin granting an elevated role is
    # legitimate and must keep working -- this isn't "nobody can ever
    # grant admin," only "a non-admin member can't."
    alice = _make_db_user(db_session, "Alice")
    dora = _make_db_user(db_session, "Dora")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id)
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(alice)

    response = client.post(
        f"/circles/{circle.id}/members",
        json={"user_id": str(dora.id), "role": "admin"},
    )

    assert response.status_code == 200
    membership = db_session.get(Membership, (circle.id, dora.id))
    assert membership is not None
    assert membership.role == "admin"
