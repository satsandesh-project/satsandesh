"""`/admin/*` in the mock chat gateway: what the console builds against. Same
rules as the real routes will have -- admin only, one audit row per write,
announcements only into announcement circles, never zero admins."""

import uuid

import pytest
from contracts.chat.admin_org import (
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
from contracts.chat.mock import admin_org as mock_admin
from contracts.chat.mock import app as mock_app
from contracts.chat.mock import claims as mock_claims
from fastapi.testclient import TestClient

ADMIN = {"X-Mock-Role": "admin", "X-Mock-User-Id": "admin-1"}


@pytest.fixture
def client():
    with TestClient(mock_app.app) as test_client:
        yield test_client
    mock_admin._users.clear()
    mock_admin._actions.clear()
    mock_admin._published.clear()
    mock_claims.reset()
    mock_app._circles.clear()
    mock_app._memberships.clear()
    mock_app._messages.clear()


def _circle(client, name="Evening Satsang", kind="group") -> AdminCircleOut:
    resp = client.post("/admin/circles", headers=ADMIN, json={"name": name, "kind": kind})
    assert resp.status_code == 201
    return AdminCircleOut.model_validate(resp.json())


def _user(client, **body) -> AdminUserCreated:
    resp = client.post("/admin/users", headers=ADMIN, json={"name": "Kamala", **body})
    assert resp.status_code == 201, resp.text
    return AdminUserCreated.model_validate(resp.json())


def _actions(client) -> list:
    return AdminActionsOut.model_validate(client.get("/admin/actions", headers=ADMIN).json()).items


@pytest.mark.parametrize("role", [None, "elder", "moderator"])
def test_every_route_refuses_a_non_admin(client, role) -> None:
    headers = {"X-Mock-Role": role} if role else {}
    someone = str(uuid.uuid4())
    for method, path, body in [
        ("get", "/admin/users", None),
        ("post", "/admin/users", {"name": "A"}),
        ("patch", f"/admin/users/{someone}/role", {"role": "admin"}),
        ("post", f"/admin/users/{someone}/token", None),
        ("post", f"/admin/users/{someone}/claim", None),
        ("get", "/admin/circles", None),
        ("post", "/admin/circles", {"name": "A"}),
        ("patch", f"/admin/circles/{someone}", {"name": "B"}),
        ("get", f"/admin/circles/{someone}/members", None),
        ("post", f"/admin/circles/{someone}/members", {"user_id": someone}),
        ("patch", f"/admin/circles/{someone}/members/{someone}", {"role": "admin"}),
        ("delete", f"/admin/circles/{someone}/members/{someone}", None),
        (
            "post",
            "/admin/announcements",
            {"text": "hi", "all_circles": True, "client_msg_id": str(uuid.uuid4())},
        ),
        ("get", "/admin/actions", None),
    ]:
        resp = client.request(method, path, headers=headers, json=body)
        assert resp.status_code == 403, (method, path)
    assert _actions(client) == []


def test_creating_a_user_returns_a_token_and_joins_the_starting_circles(client) -> None:
    circle = _circle(client)
    created = _user(client, circle_ids=[circle.id, circle.id], preferred_language="hi")
    assert created.access_token and created.user.preferred_language == "hi"
    assert created.circle_ids_joined == [circle.id]  # a repeated id joins once
    members = AdminCircleMembers.model_validate(
        client.get(f"/admin/circles/{circle.id}/members", headers=ADMIN).json()
    )
    assert [(m.user_id, m.name, m.role.value) for m in members.members] == [
        (created.user.id, "Kamala", "member")
    ]
    listed = AdminUserList.model_validate(client.get("/admin/users", headers=ADMIN).json())
    assert [u.id for u in listed.items] == [created.user.id]


def test_one_unknown_starting_circle_refuses_the_whole_request(client) -> None:
    resp = client.post(
        "/admin/users", headers=ADMIN, json={"name": "A", "circle_ids": [str(uuid.uuid4())]}
    )
    assert resp.status_code == 404
    assert client.get("/admin/users", headers=ADMIN).json()["items"] == []


def test_role_changes_and_the_last_admin_is_protected(client) -> None:
    only_admin = _user(client, role="admin").user
    resp = client.patch(f"/admin/users/{only_admin.id}/role", headers=ADMIN, json={"role": "elder"})
    assert resp.status_code == 409

    second = _user(client, role="admin").user
    resp = client.patch(f"/admin/users/{only_admin.id}/role", headers=ADMIN, json={"role": "elder"})
    assert resp.status_code == 200
    assert AdminUserOut.model_validate(resp.json()).role.value == "elder"
    resp = client.patch(f"/admin/users/{second.id}/role", headers=ADMIN, json={"role": "moderator"})
    assert resp.status_code == 409


def test_a_token_can_be_reissued(client) -> None:
    user = _user(client).user
    out = AdminTokenOut.model_validate(
        client.post(f"/admin/users/{user.id}/token", headers=ADMIN).json()
    )
    assert out.user_id == user.id and out.access_token
    assert client.post(f"/admin/users/{uuid.uuid4()}/token", headers=ADMIN).status_code == 404


def test_circles_are_created_renamed_and_listed_with_member_counts(client) -> None:
    circle = _circle(client, kind="announcement")
    renamed = client.patch(f"/admin/circles/{circle.id}", headers=ADMIN, json={"name": "News"})
    assert AdminCircleOut.model_validate(renamed.json()).name == "News"
    listed = AdminCircleList.model_validate(client.get("/admin/circles", headers=ADMIN).json())
    assert [(c.name, c.kind.value, c.member_count) for c in listed.items] == [
        ("News", "announcement", 0)
    ]
    assert (
        client.patch(
            f"/admin/circles/{uuid.uuid4()}", headers=ADMIN, json={"name": "X"}
        ).status_code
        == 404
    )


def test_membership_add_is_idempotent_then_role_change_then_remove(client) -> None:
    circle = _circle(client)
    user = _user(client).user
    path = f"/admin/circles/{circle.id}/members"

    first = client.post(path, headers=ADMIN, json={"user_id": user.id, "role": "moderator"})
    again = client.post(path, headers=ADMIN, json={"user_id": user.id, "role": "admin"})
    assert AdminMemberOut.model_validate(first.json()).role.value == "moderator"
    assert AdminMemberOut.model_validate(again.json()).role.value == "moderator"  # unchanged

    changed = client.patch(f"{path}/{user.id}", headers=ADMIN, json={"role": "member"})
    assert AdminMemberOut.model_validate(changed.json()).role.value == "member"

    assert client.delete(f"{path}/{user.id}", headers=ADMIN).status_code == 204
    assert client.delete(f"{path}/{user.id}", headers=ADMIN).status_code == 404
    assert client.post(path, headers=ADMIN, json={"user_id": str(uuid.uuid4())}).status_code == 404


def test_an_announcement_goes_only_to_announcement_circles(client) -> None:
    group = _circle(client, kind="group")
    news = _circle(client, name="News", kind="announcement")
    other_news = _circle(client, name="Events", kind="announcement")

    def post(**target):
        return client.post(
            "/admin/announcements",
            headers=ADMIN,
            json={"text": "Satsang at 6", "client_msg_id": str(uuid.uuid4()), **target},
        )

    assert post(circle_id=group.id).status_code == 422
    assert post(circle_id=str(uuid.uuid4())).status_code == 404

    one = AdminAnnouncementOut.model_validate(post(circle_id=news.id).json())
    assert one.circle_ids == [news.id] and len(one.message_ids) == 1

    everyone = AdminAnnouncementOut.model_validate(post(all_circles=True).json())
    assert sorted(everyone.circle_ids) == sorted([news.id, other_news.id])
    assert group.id not in everyone.circle_ids


def test_all_circles_with_no_announcement_circle_is_a_conflict(client) -> None:
    _circle(client, kind="group")
    resp = client.post(
        "/admin/announcements",
        headers=ADMIN,
        json={"text": "hi", "all_circles": True, "client_msg_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 409


def test_a_retried_announcement_posts_once(client) -> None:
    news = _circle(client, kind="announcement")
    body = {"text": "hi", "circle_id": news.id, "client_msg_id": str(uuid.uuid4())}
    first = client.post("/admin/announcements", headers=ADMIN, json=body).json()
    second = client.post("/admin/announcements", headers=ADMIN, json=body).json()
    assert first["message_ids"] == second["message_ids"]
    assert len(mock_app._messages[("circle", news.id)]) == 1
    assert [a.action.value for a in _actions(client)] == ["announcement.publish", "circle.create"]


def test_every_write_leaves_one_audit_row_newest_first_and_reads_leave_none(client) -> None:
    circle = _circle(client, kind="announcement")
    user = _user(client, circle_ids=[circle.id]).user
    client.patch(f"/admin/users/{user.id}/role", headers=ADMIN, json={"role": "moderator"})
    client.post(
        "/admin/announcements",
        headers=ADMIN,
        json={"text": "hi", "circle_id": circle.id, "client_msg_id": str(uuid.uuid4())},
    )
    client.get("/admin/users", headers=ADMIN)
    client.get("/admin/circles", headers=ADMIN)

    actions = _actions(client)
    assert [a.action.value for a in actions] == [
        "announcement.publish",
        "user.set_role",
        "user.create",
        "circle.create",
    ]
    assert all(a.admin_id == "admin-1" for a in actions)
    assert actions[2].details["circle_ids"] == [circle.id]
    assert "access_token" not in str(actions)
