"""The `admin_actions` table: who did what to whom, as an organisation admin.

A separate module, not more of app/db/models.py, so the feature owns its own
storage. Anything that builds the schema from `Base.metadata` (the test
fixtures' cleanup) sees this table once this module is imported -- the routes
that write it (app/admin_org.py) import it.

Append-only for real, as `moderation_events` is: a trigger (see the migration)
refuses UPDATE, DELETE and TRUNCATE. The one deliberate purge path is a
transaction-local `SET LOCAL app.allow_audit_purge = 'on'` (erasing a person's
data on request; the test fixtures' cleanup). UPDATE is refused even then.

`action` mirrors `contracts/chat/admin_org.py::AdminActionKind`; `details` is
free-form JSON for the contract's `AdminActionOut.details` and must never hold a
token or other secret. `target_id` is null only for `target_type='all_circles'`.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.id import generate_uuid7

ADMIN_ACTIONS = (
    "user.create",
    "user.set_role",
    "user.reissue_token",
    "circle.create",
    "circle.rename",
    "member.add",
    "member.set_role",
    "member.remove",
    "announcement.publish",
    "admin.promote",
)
TARGET_TYPES = ("user", "circle", "all_circles")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AdminAction(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (
        sa.CheckConstraint(
            f"action IN ({_in_list(ADMIN_ACTIONS)})", name="ck_admin_actions_action"
        ),
        sa.CheckConstraint(
            f"target_type IN ({_in_list(TARGET_TYPES)})", name="ck_admin_actions_target_type"
        ),
        sa.CheckConstraint(
            "(target_type = 'all_circles') = (target_id IS NULL)",
            name="ck_admin_actions_target_id_iff_single_target",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_admin_actions_details_object"
        ),
        sa.Index("ix_admin_actions_created_at_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
