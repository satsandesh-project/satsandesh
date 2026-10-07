"""
`/admin/*` in the mock chat gateway: what the console's Members, Circles and
Announcement pages build against before the real routes exist.

Same rules as the real gateway, so a client written against this works there:
site-admin only (`X-Mock-Role: admin`, else 403), every write appends an audit
row, an announcement goes only into `announcement` circles, and the site is never
left with no admin. In-memory, resets on restart.

Circles live in the main mock's own stores (lazy-imported, because that module
includes this router) so `GET /circles` sees what `/admin/circles` creates.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from contracts.chat.admin_org import (
    AdminActionKind,
    AdminActionOut,
    AdminActionsOut,
    AdminAnnouncementIn,
    AdminAnnouncementOut,
    AdminCircleCreate,
    AdminCircleList,
    AdminCircleMembers,
    AdminCircleOut,
    AdminCircleUpdate,
    AdminMemberAdd,
    AdminMemberOut,
    AdminMemberRoleUpdate,
    AdminTokenOut,
    AdminUserCreate,
    AdminUserCreated,
    AdminUserList,
    AdminUserOut,
    AdminUserRoleUpdate,
    SiteRole,
)
from contracts.chat.circles import Circle, CircleKind, Membership
from contracts.chat.common import MessageKind, TargetType
from contracts.chat.messages import MessageIn

router = APIRouter(prefix="/admin", tags=["admin"])

_users: dict[str, AdminUserOut] = {}
_actions: list[AdminActionOut] = []
# (admin id, client_msg_id, circle id) -> message id: a retried announcement posts once.
_published: dict[tuple[str, uuid.UUID, str], str] = {}


def _require_admin(
    x_mock_role: str | None = Header(default=None),
    x_mock_user_id: str = Header(default="mock-user-1"),
) -> str:
    if (x_mock_role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Insufficient role")
    return x_mock_user_id


def _stores():
    from contracts.chat.mock import app as main_mock

    return main_mock


def _log(admin_id: str, action: AdminActionKind, target_type: str, target_id, **details) -> None:
    _actions.append(
        AdminActionOut(
            id=str(uuid.uuid4()),
            admin_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            details=details,
            created_at=datetime.now(timezone.utc),
        )
    )


def _token_for(user_id: str) -> str:
    return f"mock-access-token-{user_id}"


def _circle(circle_id: str) -> Circle:
    circle = _stores()._circles.get(circle_id)
    if circle is None:
        raise HTTPException(status_code=404, detail="Circle not found")
    return circle


def _circle_out(circle: Circle) -> AdminCircleOut:
    return AdminCircleOut(
        id=circle.id,
        name=circle.name,
        kind=circle.kind,
        created_by=circle.created_by,
        created_at=circle.created_at,
        member_count=len(_stores()._memberships.get(circle.id, [])),
    )


def _user(user_id: str) -> AdminUserOut:
    user = _users.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _member_out(membership: Membership) -> AdminMemberOut:
    user = _users.get(membership.user_id)
    return AdminMemberOut(
        user_id=membership.user_id,
        name=user.name if user is not None else "Unknown",
        role=membership.role,
        joined_at=membership.joined_at,
    )


@router.post("/users", response_model=AdminUserCreated, status_code=201)
def create_user(body: AdminUserCreate, admin_id: str = Depends(_require_admin)) -> AdminUserCreated:
    for circle_id in body.circle_ids:
        _circle(circle_id)  # one unknown circle refuses the whole request
    user = AdminUserOut(
        id=str(uuid.uuid4()),
        name=body.name,
        preferred_language=body.preferred_language,
        role=body.role,
        created_at=datetime.now(timezone.utc),
    )
    _users[user.id] = user
    joined = list(dict.fromkeys(body.circle_ids))
    for circle_id in joined:
        _stores()._memberships.setdefault(circle_id, []).append(
            Membership(
                circle_id=circle_id,
                user_id=user.id,
                role="member",
                joined_at=datetime.now(timezone.utc),
            )
        )
    _log(
        admin_id,
        AdminActionKind.USER_CREATE,
        "user",
        user.id,
        name=user.name,
        role=user.role.value,
        circle_ids=joined,
    )
    return AdminUserCreated(user=user, access_token=_token_for(user.id), circle_ids_joined=joined)


@router.get("/users", response_model=AdminUserList)
def list_users(
    limit: int = Query(default=50, ge=1, le=200), _admin: str = Depends(_require_admin)
) -> AdminUserList:
    return AdminUserList(items=list(_users.values())[:limit])


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
def set_user_role(
    user_id: str, body: AdminUserRoleUpdate, admin_id: str = Depends(_require_admin)
) -> AdminUserOut:
    user = _user(user_id)
    if user.role is SiteRole.ADMIN and body.role is not SiteRole.ADMIN:
        if sum(1 for u in _users.values() if u.role is SiteRole.ADMIN) <= 1:
            raise HTTPException(status_code=409, detail="The site must keep at least one admin")
    updated = user.model_copy(update={"role": body.role})
    _users[user_id] = updated
    _log(
        admin_id,
        AdminActionKind.USER_SET_ROLE,
        "user",
        user_id,
        from_role=user.role.value,
        to_role=body.role.value,
    )
    return updated


@router.post("/users/{user_id}/token", response_model=AdminTokenOut)
def reissue_token(user_id: str, admin_id: str = Depends(_require_admin)) -> AdminTokenOut:
    _user(user_id)
    _log(admin_id, AdminActionKind.USER_REISSUE_TOKEN, "user", user_id)
    return AdminTokenOut(user_id=user_id, access_token=_token_for(user_id))


@router.get("/circles", response_model=AdminCircleList)
def list_circles(_admin: str = Depends(_require_admin)) -> AdminCircleList:
    return AdminCircleList(items=[_circle_out(c) for c in _stores()._circles.values()])


@router.post("/circles", response_model=AdminCircleOut, status_code=201)
def create_circle(
    body: AdminCircleCreate, admin_id: str = Depends(_require_admin)
) -> AdminCircleOut:
    circle = Circle(
        id=str(uuid.uuid4()),
        name=body.name,
        kind=body.kind,
        created_by=admin_id,
        created_at=datetime.now(timezone.utc),
    )
    _stores()._circles[circle.id] = circle
    _stores()._memberships[circle.id] = []
    _log(
        admin_id,
        AdminActionKind.CIRCLE_CREATE,
        "circle",
        circle.id,
        name=circle.name,
        kind=circle.kind.value,
    )
    return _circle_out(circle)


@router.patch("/circles/{circle_id}", response_model=AdminCircleOut)
def rename_circle(
    circle_id: str, body: AdminCircleUpdate, admin_id: str = Depends(_require_admin)
) -> AdminCircleOut:
    circle = _circle(circle_id)
    renamed = circle.model_copy(update={"name": body.name})
    _stores()._circles[circle_id] = renamed
    _log(
        admin_id,
        AdminActionKind.CIRCLE_RENAME,
        "circle",
        circle_id,
        from_name=circle.name,
        to_name=body.name,
    )
    return _circle_out(renamed)


@router.get("/circles/{circle_id}/members", response_model=AdminCircleMembers)
def list_members(circle_id: str, _admin: str = Depends(_require_admin)) -> AdminCircleMembers:
    _circle(circle_id)
    members = _stores()._memberships.get(circle_id, [])
    return AdminCircleMembers(circle_id=circle_id, members=[_member_out(m) for m in members])


def _find_membership(circle_id: str, user_id: str) -> Membership | None:
    for membership in _stores()._memberships.get(circle_id, []):
        if membership.user_id == user_id:
            return membership
    return None


@router.post("/circles/{circle_id}/members", response_model=AdminMemberOut)
def add_member(
    circle_id: str, body: AdminMemberAdd, admin_id: str = Depends(_require_admin)
) -> AdminMemberOut:
    _circle(circle_id)
    _user(body.user_id)
    existing = _find_membership(circle_id, body.user_id)
    if existing is not None:
        return _member_out(existing)
    membership = Membership(
        circle_id=circle_id,
        user_id=body.user_id,
        role=body.role,
        joined_at=datetime.now(timezone.utc),
    )
    _stores()._memberships.setdefault(circle_id, []).append(membership)
    _log(
        admin_id,
        AdminActionKind.MEMBER_ADD,
        "circle",
        circle_id,
        user_id=body.user_id,
        role=body.role.value,
    )
    return _member_out(membership)


@router.patch("/circles/{circle_id}/members/{user_id}", response_model=AdminMemberOut)
def set_member_role(
    circle_id: str,
    user_id: str,
    body: AdminMemberRoleUpdate,
    admin_id: str = Depends(_require_admin),
) -> AdminMemberOut:
    _circle(circle_id)
    existing = _find_membership(circle_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Not a member of this circle")
    updated = existing.model_copy(update={"role": body.role})
    members = _stores()._memberships[circle_id]
    members[members.index(existing)] = updated
    _log(
        admin_id,
        AdminActionKind.MEMBER_SET_ROLE,
        "circle",
        circle_id,
        user_id=user_id,
        from_role=existing.role.value,
        to_role=body.role.value,
    )
    return _member_out(updated)


@router.delete("/circles/{circle_id}/members/{user_id}", status_code=204)
def remove_member(
    circle_id: str, user_id: str, admin_id: str = Depends(_require_admin)
) -> Response:
    _circle(circle_id)
    existing = _find_membership(circle_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Not a member of this circle")
    _stores()._memberships[circle_id].remove(existing)
    _log(admin_id, AdminActionKind.MEMBER_REMOVE, "circle", circle_id, user_id=user_id)
    return Response(status_code=204)


@router.post("/announcements", response_model=AdminAnnouncementOut, status_code=201)
def publish_announcement(
    body: AdminAnnouncementIn, admin_id: str = Depends(_require_admin)
) -> AdminAnnouncementOut:
    stores = _stores()
    if body.all_circles:
        targets = [c for c in stores._circles.values() if c.kind is CircleKind.ANNOUNCEMENT]
        if not targets:
            raise HTTPException(status_code=409, detail="There is no announcement circle yet")
    else:
        circle = _circle(body.circle_id)
        if circle.kind is not CircleKind.ANNOUNCEMENT:
            raise HTTPException(status_code=422, detail="That circle is not an announcement circle")
        targets = [circle]

    message_ids = []
    for circle in targets:
        key = (admin_id, body.client_msg_id, circle.id)
        if key not in _published:
            message = stores._store_message(
                MessageIn(
                    client_msg_id=uuid.uuid5(body.client_msg_id, circle.id),
                    target_type=TargetType.CIRCLE,
                    target_id=circle.id,
                    kind=MessageKind.TEXT,
                    text=body.text,
                    source_lang=body.source_lang,
                ),
                admin_id,
            )
            _published[key] = message.id
        message_ids.append(_published[key])
    _log(
        admin_id,
        AdminActionKind.ANNOUNCEMENT_PUBLISH,
        "circle" if not body.all_circles else "all_circles",
        None if body.all_circles else body.circle_id,
        circle_ids=[c.id for c in targets],
        length=len(body.text),
    )
    return AdminAnnouncementOut(circle_ids=[c.id for c in targets], message_ids=message_ids)


@router.get("/actions", response_model=AdminActionsOut)
def list_actions(
    limit: int = Query(default=50, ge=1, le=200), _admin: str = Depends(_require_admin)
) -> AdminActionsOut:
    return AdminActionsOut(items=list(reversed(_actions))[:limit])
