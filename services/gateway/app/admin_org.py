"""Organisation admin: members, circles and announcements (`/admin/*`,
`contracts/chat/admin_org.py`). Registration is organisation-managed -- an admin
runs it from the console, not from the database.

Who may call it: a SITE admin, by the DATABASE role (`users.role == 'admin'`).
The token stub (app/auth.py) derives `role='elder'` for every legacy token, so
`require_role("admin")` could never pass; the row is the authoritative record
(the choice app/moderation.py's `require_moderator` makes too). A circle-level
admin is not a site admin.

Every write appends one `admin_actions` row (app/db/admin_actions.py) in the
SAME transaction as the change, so the log and the change commit or roll back
together. Reads log nothing. Nothing logged ever holds a token.

Three things worth knowing:

- Creating a user creates the `users` row now (the invite flow creates it only on
  activation), so the admin can set a role and circles at once. The response
  carries a signed session token for the new user.
- An announcement is an ordinary message authored by the admin in an
  `announcement` circle, created and delivered the way `POST /messages` does it
  (pipeline when enabled, then fan-out and push) but with no undo window -- an
  admin's announcement is not the elder "oops" case. The admin need not be a
  member of the circle; `can_post_to_circle` is deliberately not used, the
  site-admin check above is the authorisation.
- The site must keep at least one admin: demoting the last one is a 409. The
  first admin cannot come from the API, so `python -m app.admin_org --promote
  <user_id>` makes them (and logs it).
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime

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
)
from contracts.chat.circles import CircleKind
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import undo
from app.auth import get_current_user
from app.config import get_settings
from app.db.admin_actions import list_admin_actions, record_admin_action
from app.db.base import SessionLocal, get_db
from app.db.models import Circle, Membership
from app.db.models import User as DbUser
from app.db.repository import add_member, create_circle, create_message_with_created_flag
from app.messages import _parse_uuid, fan_out_message
from app.models import User
from app.moderation import _decode_cursor, _encode_cursor
from app.pipeline import start_pipeline
from app.tokens import issue_token
from app.users import _supported_languages

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> DbUser:
    try:
        row = db.get(DbUser, uuid.UUID(user.id))
    except ValueError:  # a non-UUID stub token has no row to be an admin
        row = None
    if row is None or row.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient role")
    return row


# --- mapping ----------------------------------------------------------------------


def _user_out(row: DbUser) -> AdminUserOut:
    return AdminUserOut(
        id=str(row.id),
        name=row.name,
        preferred_language=row.preferred_language,
        role=row.role,
        created_at=row.created_at,
    )


def _circle_out(circle: Circle, member_count: int) -> AdminCircleOut:
    return AdminCircleOut(
        id=str(circle.id),
        name=circle.name,
        kind=circle.kind,
        created_by=str(circle.created_by),
        created_at=circle.created_at,
        member_count=member_count,
    )


def _member_out(membership: Membership, user: DbUser) -> AdminMemberOut:
    return AdminMemberOut(
        user_id=str(membership.user_id),
        name=user.name,
        role=membership.role,
        joined_at=membership.joined_at,
    )


def _member_count(db: Session, circle_id: uuid.UUID) -> int:
    return db.execute(
        select(func.count()).select_from(Membership).where(Membership.circle_id == circle_id)
    ).scalar_one()


def _get_circle_or_404(db: Session, circle_id: str) -> Circle:
    circle = db.get(Circle, _parse_uuid(circle_id, field="circle_id"))
    if circle is None:
        raise HTTPException(status_code=404, detail="Circle not found")
    return circle


def _get_user_or_404(db: Session, user_id: str) -> DbUser:
    user = db.get(DbUser, _parse_uuid(user_id, field="user_id"))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _get_membership_or_404(db: Session, circle_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    membership = db.get(Membership, (circle_id, user_id))
    if membership is None:
        raise HTTPException(status_code=404, detail="Not a member of this circle")
    return membership


# --- users ------------------------------------------------------------------------


@router.post("/users", response_model=AdminUserCreated, status_code=201)
def create_user(
    body: AdminUserCreate,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserCreated:
    if body.preferred_language not in _supported_languages():
        raise HTTPException(
            status_code=422,
            detail=(
                f"preferred_language {body.preferred_language!r} is not one the pipeline "
                f"can render ({sorted(_supported_languages())})"
            ),
        )
    # One unknown circle refuses the whole request, before anything is written.
    circle_ids = list(
        dict.fromkeys(_parse_uuid(value, field="circle_ids") for value in body.circle_ids)
    )
    if circle_ids:
        found = set(db.execute(select(Circle.id).where(Circle.id.in_(circle_ids))).scalars())
        if missing := [str(c) for c in circle_ids if c not in found]:
            raise HTTPException(status_code=404, detail=f"Circle not found: {missing[0]}")

    user = DbUser(name=body.name, preferred_language=body.preferred_language, role=body.role.value)
    db.add(user)
    db.flush()
    for circle_id in circle_ids:
        add_member(db, circle_id=circle_id, user_id=user.id, role="member")
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.USER_CREATE.value,
        target_type="user",
        target_id=user.id,
        name=user.name,
        role=user.role,
        preferred_language=user.preferred_language,
        circle_ids=[str(c) for c in circle_ids],
    )
    db.commit()
    db.refresh(user)
    return AdminUserCreated(
        user=_user_out(user),
        access_token=issue_token(user.id),
        circle_ids_joined=[str(c) for c in circle_ids],
    )


@router.get("/users", response_model=AdminUserList)
def list_users(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserList:
    stmt = select(DbUser)
    if cursor is not None:
        stmt = stmt.where(DbUser.id > _decode_cursor(cursor))
    # One extra row tells us whether another page exists, without a COUNT.
    rows = list(db.execute(stmt.order_by(DbUser.id).limit(limit + 1)).scalars())
    page = rows[:limit]
    return AdminUserList(
        items=[_user_out(r) for r in page],
        next_cursor=_encode_cursor(page[-1].id) if len(rows) > limit else None,
    )


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
def set_user_role(
    user_id: str,
    body: AdminUserRoleUpdate,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    target_id = _parse_uuid(user_id, field="user_id")
    # Lock every admin row first: two admins demoting each other at the same
    # moment must not both see "someone else is still an admin".
    admins = list(
        db.execute(select(DbUser.id).where(DbUser.role == "admin").with_for_update()).scalars()
    )
    target = db.get(DbUser, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == "admin" and body.role.value != "admin" and len(admins) <= 1:
        raise HTTPException(status_code=409, detail="The site must keep at least one admin")

    previous = target.role
    target.role = body.role.value
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.USER_SET_ROLE.value,
        target_type="user",
        target_id=target.id,
        from_role=previous,
        to_role=target.role,
    )
    db.commit()
    db.refresh(target)
    return _user_out(target)


@router.post("/users/{user_id}/token", response_model=AdminTokenOut)
def reissue_token(
    user_id: str,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminTokenOut:
    user = _get_user_or_404(db, user_id)
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.USER_REISSUE_TOKEN.value,
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    return AdminTokenOut(user_id=str(user.id), access_token=issue_token(user.id))


# --- circles ----------------------------------------------------------------------


@router.get("/circles", response_model=AdminCircleList)
def list_circles(
    _admin: DbUser = Depends(require_admin), db: Session = Depends(get_db)
) -> AdminCircleList:
    counts = (
        select(Membership.circle_id, func.count().label("n"))
        .group_by(Membership.circle_id)
        .subquery()
    )
    rows = db.execute(
        select(Circle, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.circle_id == Circle.id)
        .order_by(Circle.id)
    ).all()
    return AdminCircleList(items=[_circle_out(circle, n) for circle, n in rows])


@router.post("/circles", response_model=AdminCircleOut, status_code=201)
def create_admin_circle(
    body: AdminCircleCreate,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminCircleOut:
    # Unlike POST /circles, the creating admin is NOT made a member.
    circle = create_circle(db, name=body.name, created_by=admin.id, kind=body.kind.value)
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.CIRCLE_CREATE.value,
        target_type="circle",
        target_id=circle.id,
        name=circle.name,
        kind=circle.kind,
    )
    db.commit()
    db.refresh(circle)
    return _circle_out(circle, 0)


@router.patch("/circles/{circle_id}", response_model=AdminCircleOut)
def rename_circle(
    circle_id: str,
    body: AdminCircleUpdate,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminCircleOut:
    circle = _get_circle_or_404(db, circle_id)
    previous = circle.name
    circle.name = body.name
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.CIRCLE_RENAME.value,
        target_type="circle",
        target_id=circle.id,
        from_name=previous,
        to_name=circle.name,
    )
    db.commit()
    db.refresh(circle)
    return _circle_out(circle, _member_count(db, circle.id))


@router.get("/circles/{circle_id}/members", response_model=AdminCircleMembers)
def list_members(
    circle_id: str,
    _admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminCircleMembers:
    circle = _get_circle_or_404(db, circle_id)
    rows = db.execute(
        select(Membership, DbUser)
        .join(DbUser, DbUser.id == Membership.user_id)
        .where(Membership.circle_id == circle.id)
        .order_by(Membership.joined_at, Membership.user_id)
    ).all()
    return AdminCircleMembers(
        circle_id=str(circle.id), members=[_member_out(m, u) for m, u in rows]
    )


@router.post("/circles/{circle_id}/members", response_model=AdminMemberOut)
def add_circle_member(
    circle_id: str,
    body: AdminMemberAdd,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminMemberOut:
    circle = _get_circle_or_404(db, circle_id)
    user = _get_user_or_404(db, body.user_id)
    existing = db.get(Membership, (circle.id, user.id))
    if existing is not None:
        # Already a member: nothing changes, so nothing is logged.
        return _member_out(existing, user)
    membership = add_member(db, circle_id=circle.id, user_id=user.id, role=body.role.value)
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.MEMBER_ADD.value,
        target_type="circle",
        target_id=circle.id,
        user_id=str(user.id),
        role=membership.role,
    )
    db.commit()
    db.refresh(membership)
    return _member_out(membership, user)


@router.patch("/circles/{circle_id}/members/{user_id}", response_model=AdminMemberOut)
def set_member_role(
    circle_id: str,
    user_id: str,
    body: AdminMemberRoleUpdate,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminMemberOut:
    circle = _get_circle_or_404(db, circle_id)
    membership = _get_membership_or_404(db, circle.id, _parse_uuid(user_id, field="user_id"))
    previous = membership.role
    membership.role = body.role.value
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.MEMBER_SET_ROLE.value,
        target_type="circle",
        target_id=circle.id,
        user_id=str(membership.user_id),
        from_role=previous,
        to_role=membership.role,
    )
    db.commit()
    db.refresh(membership)
    return _member_out(membership, db.get(DbUser, membership.user_id))


@router.delete("/circles/{circle_id}/members/{user_id}", status_code=204)
def remove_member(
    circle_id: str,
    user_id: str,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    circle = _get_circle_or_404(db, circle_id)
    membership = _get_membership_or_404(db, circle.id, _parse_uuid(user_id, field="user_id"))
    removed_user_id, removed_role = str(membership.user_id), membership.role
    db.delete(membership)
    record_admin_action(
        db,
        admin_id=admin.id,
        action=AdminActionKind.MEMBER_REMOVE.value,
        target_type="circle",
        target_id=circle.id,
        user_id=removed_user_id,
        role=removed_role,
    )
    db.commit()
    return Response(status_code=204)


# --- announcements ----------------------------------------------------------------


@router.post("/announcements", response_model=AdminAnnouncementOut, status_code=201)
async def publish_announcement(
    body: AdminAnnouncementIn,
    admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminAnnouncementOut:
    if body.all_circles:
        circles = list(
            db.execute(
                select(Circle)
                .where(Circle.kind == CircleKind.ANNOUNCEMENT.value)
                .order_by(Circle.id)
            ).scalars()
        )
        if not circles:
            raise HTTPException(status_code=409, detail="There is no announcement circle yet")
    else:
        circle = _get_circle_or_404(db, body.circle_id)
        if circle.kind != CircleKind.ANNOUNCEMENT.value:
            raise HTTPException(status_code=422, detail="That circle is not an announcement circle")
        circles = [circle]

    settings = get_settings()
    now = datetime.now(UTC)
    messages, created_ids = [], []
    for circle in circles:
        # One client_msg_id per author is the messages rule (a UNIQUE), and this
        # posts several messages, so each gets its own derived from the request's
        # id and the circle: a retry of the whole request maps to the same rows.
        message, created = create_message_with_created_flag(
            db,
            author_id=admin.id,
            target_type="circle",
            target_circle_id=circle.id,
            kind="text",
            text=body.text,
            source_lang=body.source_lang,
            client_msg_id=uuid.uuid5(body.client_msg_id, str(circle.id)),
            undo_expires_at=now,
        )
        if created and settings.PIPELINE_ENABLED:
            # Same transaction as the message: it never exists without its job.
            start_pipeline(db, message)
        messages.append(message)
        if created:
            created_ids.append(message.id)

    if created_ids:  # a pure retry changed nothing, so it logs nothing
        record_admin_action(
            db,
            admin_id=admin.id,
            action=AdminActionKind.ANNOUNCEMENT_PUBLISH.value,
            target_type="all_circles" if body.all_circles else "circle",
            target_id=None if body.all_circles else circles[0].id,
            circle_ids=[str(c.id) for c in circles],
            message_ids=[str(m) for m in created_ids],
            length=len(body.text),
        )
    db.commit()

    # app/ws.py imports app/messages.py at module level; this local import only
    # runs at request time, once both are fully loaded (as POST /messages does).
    from app.ws import manager

    for message_id in created_ids:
        undo.schedule_fan_out(
            str(message_id), 0, fan_out_message(message_id, manager, SessionLocal)
        )
    return AdminAnnouncementOut(
        circle_ids=[str(c.id) for c in circles], message_ids=[str(m.id) for m in messages]
    )


# --- audit log --------------------------------------------------------------------


@router.get("/actions", response_model=AdminActionsOut)
def get_actions(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    _admin: DbUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminActionsOut:
    before = _decode_cursor(cursor) if cursor is not None else None
    rows = list_admin_actions(db, before_id=before, limit=limit + 1)
    page = rows[:limit]
    return AdminActionsOut(
        items=[
            AdminActionOut(
                id=str(r.id),
                admin_id=str(r.admin_id),
                action=r.action,
                target_type=r.target_type,
                target_id=str(r.target_id) if r.target_id is not None else None,
                details=r.details,
                created_at=r.created_at,
            )
            for r in page
        ],
        next_cursor=_encode_cursor(page[-1].id) if len(rows) > limit else None,
    )


# --- bootstrap --------------------------------------------------------------------


def promote_to_admin(session: Session, user_id: uuid.UUID) -> DbUser:
    """Make an existing user the site's admin, and log it. The first admin cannot
    be made through the API (every route needs one already), so this is the way
    in. The row is its own admin_id: nobody else did it. Raises LookupError for
    an unknown user. The caller commits."""
    user = session.get(DbUser, user_id)
    if user is None:
        raise LookupError(f"no user {user_id}")
    previous = user.role
    user.role = "admin"
    record_admin_action(
        session,
        admin_id=user.id,
        action=AdminActionKind.ADMIN_PROMOTE.value,
        target_type="user",
        target_id=user.id,
        from_role=previous,
        via="cli",
    )
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make an existing user the site's admin.")
    parser.add_argument("--promote", type=uuid.UUID, required=True, metavar="USER_ID")
    args = parser.parse_args(argv)
    session = SessionLocal()
    try:
        user = promote_to_admin(session, args.promote)
        session.commit()
    except LookupError as exc:
        session.rollback()
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()
    print(f"{user.name} ({user.id}) is now an admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
