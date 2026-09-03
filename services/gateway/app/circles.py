"""`GET /circles`, `POST /circles`, `POST /circles/{id}/members`, and
`POST /circles/{id}/join` — contracts/chat/circles.py wire shapes, backed
by app/db/repository.py.

Authorization: `POST /circles/{id}/members` requires the caller to already
be a member of that circle (app/db/repository.py::is_circle_member) — same
403-for-non-member pattern app/messages.py uses for posting into a circle.
`GET /circles`/`POST /circles` need no such check: listing only ever
returns the caller's own circles (list_circles_for_user is scoped to
user_id), and creating a circle has no existing membership to require.

`POST /circles/{id}/join` (self-service, no existing membership required
— see GitHub issue #30 on this repo). Before this, the only way a second real user could end
up in a circle someone else created was an existing member calling
`POST /circles/{id}/members` with the new user's id known in advance — a
real gap once real per-user identity (UUID tokens) meant two different
sessions genuinely are different users, confirmed live (see
docs/prompt-journal.md's Month 1 close-out entry). Join always grants the
default `member` role, never anything elevated — that still requires an
existing admin via `POST /circles/{id}/members`, unchanged. Idempotent:
joining a circle you're already in returns your existing membership
rather than erroring.

Privilege-escalation fix: membership alone used to be sufficient to add a
new member with ANY role, including admin -- `MembershipCreate.role` is
fully caller-controlled (contracts/chat/circles.py), and the route never
checked the CALLER's own role before honoring it. A plain member could add
a second, colluding account directly as admin, no different check than
inviting an ordinary member. Fixed: granting anything other than the
default `member` role now requires the caller to already be an admin.
Ordinary members can still add ordinary members (unchanged) -- only the
privilege-escalation path is newly gated. No test exercised this boundary
before (every existing test grants the caller admin before calling the
route), so a new one below asserts a plain member gets 403 attempting it.
"""

import uuid

from contracts.chat.circles import (
    Circle,
    CircleCreate,
    Membership,
    MembershipCreate,
    MembershipRole,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.base import get_db
from app.db.repository import (
    add_member,
    create_circle,
    get_circle,
    get_membership,
    list_circles_for_user,
)
from app.models import User

router = APIRouter()


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be a valid UUID") from None


@router.get("/circles", response_model=list[Circle])
def get_circles(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Circle]:
    circles = list_circles_for_user(db, user_id=uuid.UUID(user.id))
    return [
        Circle(
            id=str(circle.id),
            name=circle.name,
            created_by=str(circle.created_by),
            created_at=circle.created_at,
        )
        for circle in circles
    ]


@router.post("/circles", response_model=Circle)
def post_circle(
    body: CircleCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Circle:
    caller_id = uuid.UUID(user.id)
    circle = create_circle(db, name=body.name, created_by=caller_id)
    # The creator ends up an admin member of the circle they just created —
    # confirmed decision (Step 0), not a default the reference mock shares
    # (it auto-adds nobody); see tests/test_circle_routes.py.
    add_member(db, circle_id=circle.id, user_id=caller_id, role="admin")
    db.commit()

    return Circle(
        id=str(circle.id),
        name=circle.name,
        created_by=str(circle.created_by),
        created_at=circle.created_at,
    )


@router.post("/circles/{circle_id}/members", response_model=Membership)
def post_circle_member(
    circle_id: str,
    body: MembershipCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Membership:
    circle_uuid = _parse_uuid(circle_id, field="circle_id")
    member_uuid = _parse_uuid(body.user_id, field="user_id")
    caller_id = uuid.UUID(user.id)

    caller_membership = get_membership(db, circle_id=circle_uuid, user_id=caller_id)
    if caller_membership is None:
        raise HTTPException(status_code=403, detail="Not a member of this circle")

    if body.role != MembershipRole.MEMBER and caller_membership.role != MembershipRole.ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Only a circle admin can add a member with an elevated role",
        )

    membership = add_member(db, circle_id=circle_uuid, user_id=member_uuid, role=body.role.value)
    db.commit()

    return Membership(
        circle_id=str(membership.circle_id),
        user_id=str(membership.user_id),
        role=membership.role,
        joined_at=membership.joined_at,
    )


@router.post("/circles/{circle_id}/join", response_model=Membership)
def post_circle_join(
    circle_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Membership:
    circle_uuid = _parse_uuid(circle_id, field="circle_id")
    caller_id = uuid.UUID(user.id)

    if get_circle(db, circle_uuid) is None:
        raise HTTPException(status_code=404, detail="Circle not found")

    existing = get_membership(db, circle_id=circle_uuid, user_id=caller_id)
    if existing is not None:
        return Membership(
            circle_id=str(existing.circle_id),
            user_id=str(existing.user_id),
            role=existing.role,
            joined_at=existing.joined_at,
        )

    # Always "member" -- self-join is deliberately never a path to an
    # elevated role. Granting moderator/admin still requires an existing
    # admin via POST /circles/{id}/members, unchanged.
    membership = add_member(db, circle_id=circle_uuid, user_id=caller_id, role="member")
    db.commit()

    return Membership(
        circle_id=str(membership.circle_id),
        user_id=str(membership.user_id),
        role=membership.role,
        joined_at=membership.joined_at,
    )
