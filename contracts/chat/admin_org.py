"""
Organisation admin: members, circles and announcements (`/admin/*`).

Registration is organisation-managed -- an admin runs it from the console, not
from the database. Every route here is **site-admin only** (`users.role ==
'admin'`, checked server-side against the database row; a circle-level admin is
not a site admin). Every successful write also appends one `AdminActionOut` row
to the audit log (`GET /admin/actions`).

Three decisions worth knowing before reading the shapes:

- **Creating a user creates the row now**, not on first login. That is what lets
  an admin put the person in circles and give a role straight away.
  `AdminUserCreated.access_token` is a signed session token for the new user,
  shown to the admin once (as a QR / link); it can be re-issued.
- **An announcement is a message.** There is no announcements table: publishing
  authors an ordinary message, as the admin, in a circle of kind `announcement`
  -- so it is translated, moderated and delivered like any other. `all_circles`
  posts one copy into every announcement circle (a member of two gets it twice).
- **Circle edits are rename-only.** Changing `kind` on a circle that already has
  messages would change who may post, so it is not offered.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from contracts.chat.circles import CircleKind, MembershipRole
from contracts.chat.common import VersionedModel
from contracts.chat.renderings import LANGUAGE_PATTERN

MAX_NAME_LENGTH = 120
MAX_ANNOUNCEMENT_LENGTH = 2000
MAX_STARTING_CIRCLES = 50


class SiteRole(str, Enum):
    """`users.role` -- the site-wide role. Same words as `MembershipRole`, a
    different scope (see app/db/models.py)."""

    ELDER = "elder"
    MODERATOR = "moderator"
    ADMIN = "admin"


class AdminActionKind(str, Enum):
    USER_CREATE = "user.create"
    USER_SET_ROLE = "user.set_role"
    USER_REISSUE_TOKEN = "user.reissue_token"
    CIRCLE_CREATE = "circle.create"
    CIRCLE_RENAME = "circle.rename"
    MEMBER_ADD = "member.add"
    MEMBER_SET_ROLE = "member.set_role"
    MEMBER_REMOVE = "member.remove"
    ANNOUNCEMENT_PUBLISH = "announcement.publish"
    # Written by the bootstrap CLI that makes the very first admin.
    ADMIN_PROMOTE = "admin.promote"


def _clean_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("name must not be blank")
    return value


# --- users --------------------------------------------------------------------


class AdminUserCreate(VersionedModel):
    """Request body for `POST /admin/users`."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    preferred_language: str = Field(default="te", pattern=LANGUAGE_PATTERN)
    role: SiteRole = SiteRole.ELDER
    # Circles the new member joins (as `member`) in the same step. Every id must
    # exist, or the whole request is refused and nothing is created.
    circle_ids: list[str] = Field(default_factory=list, max_length=MAX_STARTING_CIRCLES)

    _strip_name = field_validator("name")(_clean_name)


class AdminUserOut(VersionedModel):
    id: str
    name: str
    preferred_language: str
    role: SiteRole
    created_at: datetime


class AdminUserCreated(VersionedModel):
    """Response for `POST /admin/users` (201). `access_token` is the new
    member's signed session token (`Authorization: Bearer ...`)."""

    user: AdminUserOut
    access_token: str
    circle_ids_joined: list[str] = Field(default_factory=list)


class AdminUserRoleUpdate(VersionedModel):
    """Request body for `PATCH /admin/users/{id}/role`. Refused (409) if it
    would leave the site with no admin."""

    role: SiteRole


class AdminTokenOut(VersionedModel):
    """Response for `POST /admin/users/{id}/token`: a fresh session token for
    a member who lost the first one."""

    user_id: str
    access_token: str


class AdminUserList(VersionedModel):
    """Response for `GET /admin/users?cursor=&limit=`, oldest first."""

    items: list[AdminUserOut]
    next_cursor: str | None = None


# --- circles ------------------------------------------------------------------


class AdminCircleCreate(VersionedModel):
    """Request body for `POST /admin/circles`. The creating admin is NOT made a
    member (unlike `POST /circles`); add people with the members routes."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: CircleKind = CircleKind.GROUP

    _strip_name = field_validator("name")(_clean_name)


class AdminCircleUpdate(VersionedModel):
    """Request body for `PATCH /admin/circles/{id}`: rename only."""

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)

    _strip_name = field_validator("name")(_clean_name)


class AdminCircleOut(VersionedModel):
    id: str
    name: str
    kind: CircleKind
    created_by: str
    created_at: datetime
    member_count: int = Field(ge=0)


class AdminCircleList(VersionedModel):
    """Response for `GET /admin/circles` -- every circle, not just the caller's."""

    items: list[AdminCircleOut]


class AdminMemberOut(VersionedModel):
    user_id: str
    name: str
    role: MembershipRole
    joined_at: datetime


class AdminCircleMembers(VersionedModel):
    """Response for `GET /admin/circles/{id}/members`."""

    circle_id: str
    members: list[AdminMemberOut]


class AdminMemberAdd(VersionedModel):
    """Request body for `POST /admin/circles/{id}/members`. Adding someone who
    is already a member changes nothing and returns their existing row."""

    user_id: str
    role: MembershipRole = MembershipRole.MEMBER


class AdminMemberRoleUpdate(VersionedModel):
    """Request body for `PATCH /admin/circles/{id}/members/{user_id}`."""

    role: MembershipRole


# --- announcements ------------------------------------------------------------


class AdminAnnouncementIn(VersionedModel):
    """Request body for `POST /admin/announcements`. Exactly one of `circle_id`
    and `all_circles=true`. `circle_id` must be an `announcement` circle.

    `client_msg_id` makes a retry safe: the same id with the same target posts
    once. Each message's own `client_msg_id` is derived from it and the circle
    (UUIDv5), because the messages rule is one `client_msg_id` per author
    (DECISIONS.md #2) and `all_circles` posts several messages."""

    text: str = Field(min_length=1, max_length=MAX_ANNOUNCEMENT_LENGTH)
    circle_id: str | None = None
    all_circles: bool = False
    client_msg_id: UUID
    source_lang: str | None = Field(default=None, pattern=LANGUAGE_PATTERN)

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def _exactly_one_target(self) -> AdminAnnouncementIn:
        if (self.circle_id is not None) == self.all_circles:
            raise ValueError("give exactly one of circle_id and all_circles=true")
        return self


class AdminAnnouncementOut(VersionedModel):
    """Response for `POST /admin/announcements` (201). `circle_ids` and
    `message_ids` line up index for index."""

    circle_ids: list[str]
    message_ids: list[str]


# --- audit log ----------------------------------------------------------------


class AdminActionOut(VersionedModel):
    """One audit row. `details` is free-form JSON and never holds a token or
    other secret."""

    id: str
    admin_id: str
    action: AdminActionKind
    target_type: str
    target_id: str | None = None
    details: dict = Field(default_factory=dict)
    created_at: datetime


class AdminActionsOut(VersionedModel):
    """Response for `GET /admin/actions?cursor=&limit=`, newest first."""

    items: list[AdminActionOut]
    next_cursor: str | None = None
