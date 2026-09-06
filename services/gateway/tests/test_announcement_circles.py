"""Announcement circles (contracts/chat/circles.py::CircleKind.ANNOUNCEMENT):
one-to-many, moderator/admin-only posting -- proposal Section 7.1's
"announcement channels... elders mostly consume", distinct from an ordinary
'group' circle where any member can post. Reuses the exact same
membership/message/fan-out machinery a group circle already has; only
app/db/repository.py::can_post_to_circle's posting check differs.

Same TestClient + get_db/get_current_user override pattern as
tests/test_circle_routes.py and tests/test_message_routes.py.
"""

import uuid

from app.db.models import User as DbUser
from app.db.repository import add_member, create_circle


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _post_message(client, *, circle_id, text="hello"):
    return client.post(
        "/messages",
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "circle",
            "target_id": str(circle_id),
            "kind": "text",
            "text": text,
        },
    )


def test_post_circles_with_kind_announcement_creates_announcement_circle(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    response = client.post("/circles", json={"name": "Daily Thought", "kind": "announcement"})

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "announcement"


def test_post_circles_defaults_to_kind_group(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    response = client.post("/circles", json={"name": "Evening Satsang"})

    assert response.status_code == 200
    assert response.json()["kind"] == "group"


def test_get_circles_returns_kind_for_each_circle(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    group = create_circle(db_session, name="Group", created_by=alice.id, kind="group")
    announcement = create_circle(
        db_session, name="Announcements", created_by=alice.id, kind="announcement"
    )
    add_member(db_session, circle_id=group.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=announcement.id, user_id=alice.id, role="admin")
    login_as(alice)

    response = client.get("/circles")

    assert response.status_code == 200
    by_id = {c["id"]: c for c in response.json()}
    assert by_id[str(group.id)]["kind"] == "group"
    assert by_id[str(announcement.id)]["kind"] == "announcement"


def test_ordinary_member_cannot_post_to_announcement_circle(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    circle = create_circle(
        db_session, name="Daily Thought", created_by=alice.id, kind="announcement"
    )
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    login_as(bob)

    response = _post_message(client, circle_id=circle.id)

    assert response.status_code == 403
    assert "moderator or admin" in response.json()["detail"]


def test_moderator_can_post_to_announcement_circle(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    priya = _make_db_user(db_session, "Priya")
    circle = create_circle(
        db_session, name="Daily Thought", created_by=alice.id, kind="announcement"
    )
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=priya.id, role="moderator")
    login_as(priya)

    response = _post_message(client, circle_id=circle.id, text="Today's thought...")

    assert response.status_code == 200


def test_admin_can_post_to_announcement_circle(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    circle = create_circle(
        db_session, name="Daily Thought", created_by=alice.id, kind="announcement"
    )
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(alice)

    response = _post_message(client, circle_id=circle.id, text="Today's thought...")

    assert response.status_code == 200


def test_ordinary_member_can_still_post_to_a_group_circle(client, db_session, login_as):
    # Regression check: kind="group" (the default, matching every circle
    # before this field existed) must behave exactly as before -- any
    # member can post, not just moderator/admin.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    circle = create_circle(db_session, name="Evening Satsang", created_by=alice.id, kind="group")
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    login_as(bob)

    response = _post_message(client, circle_id=circle.id)

    assert response.status_code == 200


def test_ordinary_member_can_still_read_an_announcement_circle(client, db_session, login_as):
    # Posting is gated; reading/syncing is not -- "elders mostly consume"
    # means they read every message, they just don't author them.
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    circle = create_circle(
        db_session, name="Daily Thought", created_by=alice.id, kind="announcement"
    )
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    add_member(db_session, circle_id=circle.id, user_id=bob.id, role="member")
    login_as(alice)
    _post_message(client, circle_id=circle.id, text="Today's thought...")

    login_as(bob)
    response = client.get(
        "/messages", params={"target_type": "circle", "target_id": str(circle.id)}
    )

    assert response.status_code == 200
    assert len(response.json()["messages"]) == 1


def test_non_member_cannot_post_to_announcement_circle(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    stranger = _make_db_user(db_session, "Stranger")
    circle = create_circle(
        db_session, name="Daily Thought", created_by=alice.id, kind="announcement"
    )
    add_member(db_session, circle_id=circle.id, user_id=alice.id, role="admin")
    login_as(stranger)

    response = _post_message(client, circle_id=circle.id)

    assert response.status_code == 403
    # A non-member gets the original "not a member" message, not the
    # announcement-specific one -- they're not being told they're the
    # wrong role, they're not in the circle at all.
    assert response.json()["detail"] == "Not a member of this circle"
