from datetime import datetime
from enum import Enum

from contracts.chat.common import VersionedModel


class MembershipRole(str, Enum):
    MEMBER = "member"
    MODERATOR = "moderator"
    ADMIN = "admin"


class CircleKind(str, Enum):
    """GROUP: any member can post, matching every circle before this field
    existed (the default, for backward compatibility with existing rows).
    ANNOUNCEMENT: one-to-many — only a moderator or admin member can post
    (app/db/repository.py::can_post_to_circle), everyone else reads only.
    Proposal Section 7.1's "announcement channels — one-to-many streams
    that elders mostly consume", distinct from a `circles` (group) chat.
    Deliberately a property of the circle, not a separate table: reuses the
    exact same membership/role/message/fan-out machinery a group circle
    already has — the only thing that differs is who's allowed to post."""

    GROUP = "group"
    ANNOUNCEMENT = "announcement"


class Circle(VersionedModel):
    id: str
    name: str
    kind: CircleKind = CircleKind.GROUP
    created_by: str
    created_at: datetime


class CircleCreate(VersionedModel):
    """Request body for `POST /circles`. `id`/`created_by`/`created_at` are
    server-assigned — `created_by` comes from the authenticated caller, not
    a client-supplied field, so it can't be spoofed."""

    name: str
    kind: CircleKind = CircleKind.GROUP


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
