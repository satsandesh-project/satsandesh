"""Organisation admin (`/admin/*`, app/admin_org.py): an admin onboards a member
without touching the database.

The role checked is the DATABASE role (`users.role`): stub auth derives
'elder' from any legacy token, so `require_role("admin")` could never pass.
These tests authenticate with real signed tokens (`issue_token`), which the
`client` fixture leaves un-overridden, so the whole auth path is exercised --
including the new member's own token working.

Every write must leave exactly one `admin_actions` row, in the same
transaction; reads leave none.
"""

import time
import uuid

import pytest
from contracts.chat.admin_org import (
    AdminActionKind,
    AdminActionsOut,
    AdminAnnouncementOut,
    AdminCircleList,
    AdminCircleMembers,
    AdminCircleOut,
    AdminMemberOut,
    AdminTokenOut,
    AdminUserCreated,
    AdminUserList,
    AdminUserOut,
)
from sqlalchemy.orm import sessionmaker

import app.ws as ws_module
from app.admin_org import promote_to_admin
from app.db.admin_actions import ADMIN_ACTIONS, AdminAction
from app.db.models import Circle, Membership, Message
from app.db.models import User as DbUser
from app.tokens import issue_token, verify_token


def _user(db_session, name="Person", role="elder", language="te"):
    user = DbUser(name=name, preferred_language=language, role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _as(user):
    return {"Authorization": f"Bearer {issue_token(user.id)}"}


@pytest.fixture
def admin(db_session):
    return _user(db_session, "Asha", role="admin")


@pytest.fixture
def h(admin):
    return _as(admin)


def _circle(db_session, creator, name="Evening Satsang", kind="group"):
    circle = Circle(name=name, kind=kind, created_by=creator.id)
    db_session.add(circle)
    db_session.commit()
    return circle


def _actions(db_session):
    db_session.expire_all()
    return list(db_session.query(AdminAction).order_by(AdminAction.id))


def _kinds(db_session):
    return [a.action for a in _actions(db_session)]


# --- who may use it -----------------------------------------------------------------------


def _every_route(someone):
    return [
        ("get", "/admin/users", None),
        ("post", "/admin/users", {"name": "A"}),
        ("patch", f"/admin/users/{someone}/role", {"role": "admin"}),
        ("post", f"/admin/users/{someone}/token", None),
        ("get", "/admin/circles", None),
        ("post", "/admin/circles", {"name": "A"}),
        ("patch", f"/admin/circles/{someone}", {"name": "B"}),
        ("get", f"/admin/circles/{someone}/members", None),
        ("post", f"/admin/circles/{someone}/members", {"user_id": str(someone)}),
        ("patch", f"/admin/circles/{someone}/members/{someone}", {"role": "admin"}),
        ("delete", f"/admin/circles/{someone}/members/{someone}", None),
        (
            "post",
            "/admin/announcements",
            {"text": "hi", "all_circles": True, "client_msg_id": str(uuid.uuid4())},
        ),
        ("get", "/admin/actions", None),
    ]


def test_no_token_is_401(client):
    for method, path, body in _every_route(uuid.uuid4()):
        assert client.request(method, path, json=body).status_code == 401, path


@pytest.mark.parametrize("role", ["elder", "moderator"])
def test_a_non_admin_is_refused_every_route(client, db_session, role):
    caller = _user(db_session, "Not admin", role=role)
    for method, path, body in _every_route(uuid.uuid4()):
        resp = client.request(method, path, headers=_as(caller), json=body)
        assert resp.status_code == 403, (method, path)
    assert _actions(db_session) == []
    assert db_session.query(DbUser).count() == 1  # nothing was created


def test_a_circle_admin_is_not_a_site_admin(client, db_session):
    caller = _user(db_session, "Circle admin")  # site role: elder
    circle = _circle(db_session, caller)
    db_session.add(Membership(circle_id=circle.id, user_id=caller.id, role="admin"))
    db_session.commit()

    assert client.get("/admin/circles", headers=_as(caller)).status_code == 403


def test_a_bare_uuid_token_is_refused_even_for_a_real_admin(client, db_session, admin):
    """The default AUTH_MODE is `legacy`, where a bare UUID *is* that user. These
    routes create users and message everyone, so knowing an admin's id must not
    be enough: 401, before the stub that would provision a row for it."""
    legacy = {"Authorization": f"Bearer {admin.id}"}
    for method, path, body in _every_route(uuid.uuid4()):
        resp = client.request(method, path, headers=legacy, json=body)
        assert resp.status_code == 401, (method, path)
        assert resp.json()["detail"] == "Admin routes require a signed token"
    stranger = uuid.uuid4()
    resp = client.get("/admin/users", headers={"Authorization": f"Bearer {stranger}"})
    assert resp.status_code == 401
    assert db_session.get(DbUser, stranger) is None, "a refused token provisions no user"
    assert db_session.query(DbUser).count() == 1 and _actions(db_session) == []


def test_the_database_role_decides_and_a_demotion_applies_on_the_next_request(
    client, db_session, admin
):
    other = _user(db_session, "Second", role="admin")
    assert client.get("/admin/users", headers=_as(admin)).status_code == 200

    resp = client.patch(f"/admin/users/{admin.id}/role", headers=_as(other), json={"role": "elder"})
    assert resp.status_code == 200
    assert client.get("/admin/users", headers=_as(admin)).status_code == 403


# --- users: the create flow -----------------------------------------------------------------


def test_an_admin_onboards_a_member_without_touching_the_database(client, db_session, admin, h):
    circle = _circle(db_session, admin)

    resp = client.post(
        "/admin/users",
        headers=h,
        json={"name": "  Kamala Devi ", "preferred_language": "hi", "circle_ids": [str(circle.id)]},
    )

    assert resp.status_code == 201
    created = AdminUserCreated.model_validate(resp.json())
    assert created.user.name == "Kamala Devi"
    assert (created.user.role.value, created.user.preferred_language) == ("elder", "hi")
    assert created.circle_ids_joined == [str(circle.id)]

    # The row and the membership are real ...
    stored = db_session.get(DbUser, uuid.UUID(created.user.id))
    assert stored is not None and stored.role == "elder"
    membership = db_session.get(Membership, (circle.id, stored.id))
    assert membership is not None and membership.role == "member"

    # ... and the member's own token works as them.
    assert verify_token(created.access_token) == stored.id
    me = client.get("/me", headers={"Authorization": f"Bearer {created.access_token}"})
    assert me.status_code == 200 and me.json()["id"] == created.user.id

    (row,) = _actions(db_session)
    assert row.action == "user.create" and row.admin_id == admin.id
    assert (row.target_type, row.target_id) == ("user", stored.id)
    assert row.details["circle_ids"] == [str(circle.id)]
    assert created.access_token not in str(row.details), "a token must never reach the log"


def test_a_new_user_defaults_to_an_elder_who_speaks_telugu(client, db_session, h):
    resp = client.post("/admin/users", headers=h, json={"name": "Ravi"})
    out = AdminUserCreated.model_validate(resp.json())
    assert (out.user.role.value, out.user.preferred_language, out.circle_ids_joined) == (
        "elder",
        "te",
        [],
    )


def test_a_user_can_be_created_with_a_role(client, db_session, h):
    resp = client.post("/admin/users", headers=h, json={"name": "Mod", "role": "moderator"})
    assert AdminUserCreated.model_validate(resp.json()).user.role.value == "moderator"


def test_one_unknown_circle_refuses_the_whole_request(client, db_session, admin, h):
    real = _circle(db_session, admin)
    resp = client.post(
        "/admin/users",
        headers=h,
        json={"name": "Kamala", "circle_ids": [str(real.id), str(uuid.uuid4())]},
    )
    assert resp.status_code == 404
    assert db_session.query(DbUser).count() == 1  # still just the admin
    assert db_session.query(Membership).count() == 0
    assert _actions(db_session) == []


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "A", "role": "superuser"},
        {"name": "A", "preferred_language": "Hindi"},
        {"name": "A", "preferred_language": "xx"},  # well-formed, but not renderable
        {"name": "A", "circle_ids": ["not-a-uuid"]},
    ],
)
def test_a_bad_request_creates_nothing(client, db_session, h, body):
    assert client.post("/admin/users", headers=h, json=body).status_code == 422
    assert db_session.query(DbUser).count() == 1
    assert _actions(db_session) == []


def test_a_repeated_circle_id_joins_once(client, db_session, admin, h):
    circle = _circle(db_session, admin)
    resp = client.post(
        "/admin/users", headers=h, json={"name": "A", "circle_ids": [str(circle.id)] * 2}
    )
    assert resp.status_code == 201
    assert db_session.query(Membership).count() == 1


def test_users_are_listed_and_paged(client, db_session, admin, h):
    for i in range(3):
        _user(db_session, f"Member {i}")  # 4 users in all

    first = AdminUserList.model_validate(client.get("/admin/users?limit=3", headers=h).json())
    assert len(first.items) == 3 and first.next_cursor is not None
    second = AdminUserList.model_validate(
        client.get(f"/admin/users?limit=3&cursor={first.next_cursor}", headers=h).json()
    )
    assert len(second.items) == 1 and second.next_cursor is None
    ids = [u.id for u in first.items + second.items]
    assert len(set(ids)) == 4 and str(admin.id) in ids
    assert client.get("/admin/users?cursor=garbage", headers=h).status_code == 422
    assert _actions(db_session) == [], "reads log nothing"


# --- users: roles and tokens ------------------------------------------------------------------


def test_an_admin_sets_a_role_and_it_is_logged(client, db_session, admin, h):
    member = _user(db_session, "Member")
    resp = client.patch(f"/admin/users/{member.id}/role", headers=h, json={"role": "moderator"})
    assert resp.status_code == 200
    assert AdminUserOut.model_validate(resp.json()).role.value == "moderator"
    db_session.expire_all()
    assert db_session.get(DbUser, member.id).role == "moderator"
    (row,) = _actions(db_session)
    assert row.action == "user.set_role" and row.target_id == member.id
    assert row.details == {"from_role": "elder", "to_role": "moderator"}


def test_the_last_admin_cannot_be_demoted(client, db_session, admin, h):
    resp = client.patch(f"/admin/users/{admin.id}/role", headers=h, json={"role": "elder"})
    assert resp.status_code == 409
    db_session.expire_all()
    assert db_session.get(DbUser, admin.id).role == "admin"
    assert _actions(db_session) == []


def test_with_a_second_admin_one_may_be_demoted_but_not_both(client, db_session, admin, h):
    second = _user(db_session, "Second", role="admin")
    assert (
        client.patch(
            f"/admin/users/{second.id}/role", headers=h, json={"role": "elder"}
        ).status_code
        == 200
    )
    resp = client.patch(f"/admin/users/{admin.id}/role", headers=h, json={"role": "elder"})
    assert resp.status_code == 409


def test_a_role_change_for_an_unknown_user_is_404(client, h):
    resp = client.patch(f"/admin/users/{uuid.uuid4()}/role", headers=h, json={"role": "admin"})
    assert resp.status_code == 404
    assert (
        client.patch("/admin/users/nope/role", headers=h, json={"role": "admin"}).status_code == 422
    )


def test_a_lost_token_can_be_reissued_and_the_log_holds_no_secret(client, db_session, h):
    member = _user(db_session, "Member")
    resp = client.post(f"/admin/users/{member.id}/token", headers=h)
    out = AdminTokenOut.model_validate(resp.json())
    assert out.user_id == str(member.id) and verify_token(out.access_token) == member.id
    (row,) = _actions(db_session)
    assert row.action == "user.reissue_token" and out.access_token not in str(row.details)
    assert client.post(f"/admin/users/{uuid.uuid4()}/token", headers=h).status_code == 404


# --- circles ------------------------------------------------------------------------------------


def test_an_admin_creates_a_circle_without_joining_it(client, db_session, admin, h):
    resp = client.post(
        "/admin/circles", headers=h, json={"name": "  Bhajan Mandali ", "kind": "announcement"}
    )
    assert resp.status_code == 201
    out = AdminCircleOut.model_validate(resp.json())
    assert (out.name, out.kind.value, out.member_count) == ("Bhajan Mandali", "announcement", 0)
    assert out.created_by == str(admin.id)
    assert db_session.query(Membership).count() == 0
    (row,) = _actions(db_session)
    assert row.action == "circle.create" and row.target_id == uuid.UUID(out.id)


def test_circles_are_listed_with_member_counts_including_ones_the_admin_is_not_in(
    client, db_session, admin, h
):
    other = _user(db_session, "Other")
    mine = _circle(db_session, admin, "Mine")
    theirs = _circle(db_session, other, "Theirs", kind="announcement")
    empty = _circle(db_session, other, "Empty")
    db_session.add_all(
        [
            Membership(circle_id=mine.id, user_id=admin.id, role="admin"),
            Membership(circle_id=mine.id, user_id=other.id, role="member"),
            Membership(circle_id=theirs.id, user_id=other.id, role="member"),
        ]
    )
    db_session.commit()

    listed = AdminCircleList.model_validate(client.get("/admin/circles", headers=h).json())
    counts = {c.name: (c.kind.value, c.member_count) for c in listed.items}
    assert counts == {"Mine": ("group", 2), "Theirs": ("announcement", 1), "Empty": ("group", 0)}
    assert empty.id and _actions(db_session) == []


def test_a_circle_is_renamed_and_its_kind_is_left_alone(client, db_session, admin, h):
    circle = _circle(db_session, admin, "Old", kind="announcement")
    resp = client.patch(f"/admin/circles/{circle.id}", headers=h, json={"name": "New"})
    assert resp.status_code == 200
    out = AdminCircleOut.model_validate(resp.json())
    assert (out.name, out.kind.value) == ("New", "announcement")
    (row,) = _actions(db_session)
    assert row.action == "circle.rename" and row.details == {"from_name": "Old", "to_name": "New"}
    # Rename-only: a kind in the body is not a way to change it.
    client.patch(f"/admin/circles/{circle.id}", headers=h, json={"name": "N2", "kind": "group"})
    db_session.expire_all()
    assert db_session.get(Circle, circle.id).kind == "announcement"
    assert (
        client.patch(f"/admin/circles/{uuid.uuid4()}", headers=h, json={"name": "X"}).status_code
        == 404
    )


# --- circles: membership ------------------------------------------------------------------------


def test_membership_is_added_changed_and_removed_with_one_log_row_each(
    client, db_session, admin, h
):
    circle = _circle(db_session, admin)
    member = _user(db_session, "Member")
    path = f"/admin/circles/{circle.id}/members"

    added = client.post(path, headers=h, json={"user_id": str(member.id)})
    assert added.status_code == 200
    assert AdminMemberOut.model_validate(added.json()).role.value == "member"

    changed = client.patch(f"{path}/{member.id}", headers=h, json={"role": "moderator"})
    assert AdminMemberOut.model_validate(changed.json()).role.value == "moderator"
    db_session.expire_all()
    assert db_session.get(Membership, (circle.id, member.id)).role == "moderator"

    members = AdminCircleMembers.model_validate(client.get(path, headers=h).json())
    assert [(m.name, m.role.value) for m in members.members] == [("Member", "moderator")]

    assert client.delete(f"{path}/{member.id}", headers=h).status_code == 204
    db_session.expire_all()
    assert db_session.get(Membership, (circle.id, member.id)) is None
    assert db_session.get(DbUser, member.id) is not None, "removing from a circle keeps the person"

    assert _kinds(db_session) == ["member.add", "member.set_role", "member.remove"]
    assert all(a.target_id == circle.id for a in _actions(db_session))


def test_adding_an_existing_member_changes_and_logs_nothing(client, db_session, admin, h):
    circle = _circle(db_session, admin)
    member = _user(db_session, "Member")
    path = f"/admin/circles/{circle.id}/members"
    client.post(path, headers=h, json={"user_id": str(member.id), "role": "moderator"})

    again = client.post(path, headers=h, json={"user_id": str(member.id), "role": "admin"})

    assert again.status_code == 200
    assert AdminMemberOut.model_validate(again.json()).role.value == "moderator"
    assert _kinds(db_session) == ["member.add"]


def test_membership_errors(client, db_session, admin, h):
    circle = _circle(db_session, admin)
    member = _user(db_session, "Member")
    path = f"/admin/circles/{circle.id}/members"
    ghost = uuid.uuid4()

    assert client.post(path, headers=h, json={"user_id": str(ghost)}).status_code == 404
    assert client.post(path, headers=h, json={"user_id": "nope"}).status_code == 422
    unknown = f"/admin/circles/{ghost}/members"
    assert client.post(unknown, headers=h, json={"user_id": str(member.id)}).status_code == 404
    assert client.get(unknown, headers=h).status_code == 404
    # Not a member -> nothing to change or remove.
    assert client.patch(f"{path}/{member.id}", headers=h, json={"role": "admin"}).status_code == 404
    assert client.delete(f"{path}/{member.id}", headers=h).status_code == 404
    assert _actions(db_session) == []


def test_a_member_added_by_an_admin_can_read_the_circle(client, db_session, admin, h):
    """The point of adding someone: they can then use the circle."""
    circle = _circle(db_session, admin)
    created = AdminUserCreated.model_validate(
        client.post(
            "/admin/users", headers=h, json={"name": "Kamala", "circle_ids": [str(circle.id)]}
        ).json()
    )
    resp = client.get(
        f"/messages?target_type=circle&target_id={circle.id}",
        headers={"Authorization": f"Bearer {created.access_token}"},
    )
    assert resp.status_code == 200


# --- announcements ------------------------------------------------------------------------------


@pytest.fixture
def delivered(monkeypatch, engine):
    """An announcement's fan-out runs as a scheduled task on its own session:
    collapse the sleep, point that session at the test database, and record the
    frames the websocket manager is asked to send."""
    import app.admin_org as admin_module
    import app.undo as undo_module

    monkeypatch.setattr(
        admin_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
    )

    async def instant(_delay):
        return None

    monkeypatch.setattr(undo_module, "asyncio_sleep", instant)
    frames = []

    async def capture(recipients, frame, exclude=None):
        frames.append((list(recipients), frame))

    monkeypatch.setattr(ws_module.manager, "broadcast", capture)
    return frames


def _announce(client, h, **body):
    body.setdefault("text", "Satsang moves to 6 pm on Sunday.")
    body.setdefault("client_msg_id", str(uuid.uuid4()))
    return client.post("/admin/announcements", headers=h, json=body)


def _settle(db_session, message_id, status="sent", timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db_session.expire_all()
        if db_session.get(Message, message_id).status == status:
            return True
        time.sleep(0.02)
    return False


def test_an_announcement_reaches_the_circles_members_though_the_admin_is_not_one(
    client, db_session, admin, h, delivered
):
    news = _circle(db_session, admin, "News", kind="announcement")
    reader = _user(db_session, "Reader")
    db_session.add(Membership(circle_id=news.id, user_id=reader.id, role="member"))
    db_session.commit()

    resp = _announce(client, h, circle_id=str(news.id))

    assert resp.status_code == 201
    out = AdminAnnouncementOut.model_validate(resp.json())
    assert out.circle_ids == [str(news.id)] and len(out.message_ids) == 1
    message_id = uuid.UUID(out.message_ids[0])
    assert _settle(db_session, message_id), "the announcement was never delivered"

    message = db_session.get(Message, message_id)
    assert message.author_id == admin.id and message.target_circle_id == news.id
    assert message.text == "Satsang moves to 6 pm on Sunday."
    (recipients, frame) = next(f for f in delivered if f[1]["type"] == "message.new")
    assert recipients == [str(reader.id)] and frame["data"]["id"] == out.message_ids[0]

    (row,) = _actions(db_session)
    assert row.action == "announcement.publish" and row.target_id == news.id
    assert row.details["message_ids"] == out.message_ids


def test_a_reader_can_fetch_the_announcement(client, db_session, admin, h, delivered):
    news = _circle(db_session, admin, "News", kind="announcement")
    reader = _user(db_session, "Reader")
    db_session.add(Membership(circle_id=news.id, user_id=reader.id, role="member"))
    db_session.commit()
    out = AdminAnnouncementOut.model_validate(_announce(client, h, circle_id=str(news.id)).json())
    assert _settle(db_session, uuid.UUID(out.message_ids[0]))

    resp = client.get(f"/messages?target_type=circle&target_id={news.id}", headers=_as(reader))

    assert [m["id"] for m in resp.json()["messages"]] == out.message_ids


def test_an_announcement_only_goes_into_an_announcement_circle(
    client, db_session, admin, h, delivered
):
    group = _circle(db_session, admin, "Chat", kind="group")
    assert _announce(client, h, circle_id=str(group.id)).status_code == 422
    assert _announce(client, h, circle_id=str(uuid.uuid4())).status_code == 404
    assert _announce(client, h, circle_id="nope").status_code == 422
    assert db_session.query(Message).count() == 0 and _actions(db_session) == []


def test_all_circles_posts_one_copy_into_each_announcement_circle(
    client, db_session, admin, h, delivered
):
    news = _circle(db_session, admin, "News", kind="announcement")
    events = _circle(db_session, admin, "Events", kind="announcement")
    chat = _circle(db_session, admin, "Chat", kind="group")

    out = AdminAnnouncementOut.model_validate(_announce(client, h, all_circles=True).json())

    assert sorted(out.circle_ids) == sorted([str(news.id), str(events.id)])
    assert str(chat.id) not in out.circle_ids and len(set(out.message_ids)) == 2
    (row,) = _actions(db_session)
    assert (row.target_type, row.target_id) == ("all_circles", None)
    assert sorted(row.details["circle_ids"]) == sorted(out.circle_ids)


def test_all_circles_with_no_announcement_circle_is_a_conflict(
    client, db_session, admin, h, delivered
):
    _circle(db_session, admin, "Chat", kind="group")
    assert _announce(client, h, all_circles=True).status_code == 409
    assert db_session.query(Message).count() == 0


@pytest.mark.parametrize(
    "target", [{}, {"circle_id": "c", "all_circles": True}], ids=["neither", "both"]
)
def test_an_announcement_needs_exactly_one_target(client, h, target):
    assert _announce(client, h, **target).status_code == 422


def test_a_retried_announcement_posts_and_logs_once(client, db_session, admin, h, delivered):
    news = _circle(db_session, admin, "News", kind="announcement")
    body = {"circle_id": str(news.id), "client_msg_id": str(uuid.uuid4())}

    first = _announce(client, h, **body).json()
    second = _announce(client, h, **body).json()

    assert first["message_ids"] == second["message_ids"]
    assert db_session.query(Message).count() == 1
    assert _kinds(db_session) == ["announcement.publish"]


def test_the_same_request_id_across_circles_does_not_collide(
    client, db_session, admin, h, delivered
):
    """One client_msg_id per author is a UNIQUE on messages; all_circles posts
    several messages from one request id, so each gets its own derived id."""
    _circle(db_session, admin, "News", kind="announcement")
    _circle(db_session, admin, "Events", kind="announcement")
    assert _announce(client, h, all_circles=True).status_code == 201
    assert db_session.query(Message).count() == 2


# --- the audit log ------------------------------------------------------------------------------


def test_every_write_leaves_exactly_one_row_newest_first_and_reads_leave_none(
    client, db_session, admin, h, delivered
):
    news = _circle(db_session, admin, "News", kind="announcement")
    created = AdminUserCreated.model_validate(
        client.post(
            "/admin/users", headers=h, json={"name": "Kamala", "circle_ids": [str(news.id)]}
        ).json()
    )
    client.patch(f"/admin/users/{created.user.id}/role", headers=h, json={"role": "moderator"})
    client.post("/admin/circles", headers=h, json={"name": "Chat"})
    _announce(client, h, circle_id=str(news.id))
    client.get("/admin/users", headers=h)
    client.get("/admin/circles", headers=h)
    client.get(f"/admin/circles/{news.id}/members", headers=h)

    listed = AdminActionsOut.model_validate(client.get("/admin/actions", headers=h).json())

    assert [a.action.value for a in listed.items] == [
        "announcement.publish",
        "circle.create",
        "user.set_role",
        "user.create",
    ]
    assert {a.admin_id for a in listed.items} == {str(admin.id)}
    assert created.access_token not in str(listed.model_dump(mode="json"))


def test_the_log_is_paged_newest_first(client, db_session, admin, h):
    for i in range(3):
        client.post("/admin/circles", headers=h, json={"name": f"C{i}"})

    first = AdminActionsOut.model_validate(client.get("/admin/actions?limit=2", headers=h).json())
    assert [a.details["name"] for a in first.items] == ["C2", "C1"]
    second = AdminActionsOut.model_validate(
        client.get(f"/admin/actions?limit=2&cursor={first.next_cursor}", headers=h).json()
    )
    assert [a.details["name"] for a in second.items] == ["C0"] and second.next_cursor is None


def test_a_failed_write_leaves_no_log_row(client, db_session, h):
    """The log line and the change commit together: a refused request writes
    neither."""
    client.post("/admin/users", headers=h, json={"name": "A", "circle_ids": [str(uuid.uuid4())]})
    client.patch(f"/admin/users/{uuid.uuid4()}/role", headers=h, json={"role": "admin"})
    assert _actions(db_session) == []


# --- the bootstrap -----------------------------------------------------------------------------


def test_promoting_the_first_admin_is_logged_against_themselves(db_session):
    person = _user(db_session, "First")

    promoted = promote_to_admin(db_session, person.id)
    db_session.commit()

    assert promoted.role == "admin"
    (row,) = _actions(db_session)
    assert row.action == "admin.promote" and row.admin_id == person.id == row.target_id
    assert row.details == {"from_role": "elder", "via": "cli"}


def test_promoting_an_unknown_user_is_refused(db_session):
    with pytest.raises(LookupError):
        promote_to_admin(db_session, uuid.uuid4())
    assert _actions(db_session) == []


def test_a_freshly_promoted_admin_can_use_the_api(client, db_session):
    person = _user(db_session, "First")
    assert client.get("/admin/users", headers=_as(person)).status_code == 403
    promote_to_admin(db_session, person.id)
    db_session.commit()
    assert client.get("/admin/users", headers=_as(person)).status_code == 200


# --- the three lists that must agree ----------------------------------------------------------


def test_the_stored_action_names_are_the_contracts():
    assert set(ADMIN_ACTIONS) == {kind.value for kind in AdminActionKind}
