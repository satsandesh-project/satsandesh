"""`GET /circles`, `POST /circles`, and `POST /circles/{id}/members` —
contracts/chat/circles.py wire shapes, backed by app/db/repository.py.

Authorization: `POST /circles/{id}/members` requires the caller to already
be a member of that circle (app/db/repository.py::is_circle_member) — same
403-for-non-member pattern app/messages.py uses for posting into a circle.
`GET /circles`/`POST /circles` need no such check: listing only ever
returns the caller's own circles (list_circles_for_user is scoped to
user_id), and creating a circle has no existing membership to require.
"""

import uuid

from contracts.chat.circles import Circle, CircleCreate, Membership, MembershipCreate
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.base import get_db
from app.db.repository import add_member, create_circle, is_circle_member, list_circles_for_user
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

    if not is_circle_member(db, circle_id=circle_uuid, user_id=caller_id):
        raise HTTPException(status_code=403, detail="Not a member of this circle")

    membership = add_member(db, circle_id=circle_uuid, user_id=member_uuid, role=body.role.value)
    db.commit()

    return Membership(
        circle_id=str(membership.circle_id),
        user_id=str(membership.user_id),
        role=membership.role,
        joined_at=membership.joined_at,
    )
