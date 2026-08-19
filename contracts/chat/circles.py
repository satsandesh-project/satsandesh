from datetime import datetime
from enum import Enum

from contracts.chat.common import VersionedModel


class MembershipRole(str, Enum):
    MEMBER = "member"
    MODERATOR = "moderator"
    ADMIN = "admin"


class Circle(VersionedModel):
    id: str
    name: str
    created_by: str
    created_at: datetime


class CircleCreate(VersionedModel):
    """Request body for `POST /circles`. `id`/`created_by`/`created_at` are
    server-assigned — `created_by` comes from the authenticated caller, not
    a client-supplied field, so it can't be spoofed."""

    name: str


class Membership(VersionedModel):
    circle_id: str
    user_id: str
    role: MembershipRole
    joined_at: datetime


class MembershipCreate(VersionedModel):
    """Request body for `POST /circles/{id}/members`. `circle_id` comes from
    the URL path, not the body, so it can't disagree with the resource being
    posted to."""

    user_id: str
    role: MembershipRole = MembershipRole.MEMBER
