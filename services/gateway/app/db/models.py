"""SQLAlchemy 2.0 models for docs/SCHEMA_DRAFT.md's `users`, `circles`,
`memberships`, `conversations`, and `messages` tables.

Every column, type, nullability, default, `CHECK`, `UNIQUE`, and FK
`ON DELETE` clause here is meant to match that document exactly, as
resolved by the team's Week 2 schema review. Where a comment cites a
"design question" or index number, it's pointing at the specific section of
`docs/SCHEMA_DRAFT.md` that section of DDL implements, so a future reader
doesn't have to reverse-engineer why a constraint exists.

One FK detail the schema doc left unresolved: `ON DELETE` behavior for
`messages.target_user_id`, `messages.target_circle_id`, and
`conversations.user_a`/`user_b`. All four are set to `RESTRICT` here,
matching the doc's own established pattern for every other FK to `users.id`
(`circles.created_by`, `memberships.user_id`, `messages.author_id`) — the
one exception in the whole schema is `memberships.circle_id`, which
`CASCADE`s because a membership can't outlive its circle.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.id import generate_uuid7


class User(Base):
    """docs/SCHEMA_DRAFT.md `users` table."""

    __tablename__ = "users"
    __table_args__ = (
        # `users` table, `role` column — the global/site role. Deliberately
        # the same vocabulary as Membership.role's per-circle role below,
        # different scope; see the schema doc's note on that.
        sa.CheckConstraint("role IN ('elder', 'moderator', 'admin')", name="ck_users_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    photo: Mapped[str | None] = mapped_column(sa.Text)
    preferred_language: Mapped[str] = mapped_column(sa.Text, nullable=False)
    tts_on: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    role: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'elder'"))
    # IANA zone name (e.g. "Asia/Kolkata"), never a UTC offset — design
    # question #4. No DB-level format constraint (Postgres has no built-in
    # domain for it); validate at the application layer later.
    timezone: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Circle(Base):
    """docs/SCHEMA_DRAFT.md `circles` table."""

    __tablename__ = "circles"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Membership(Base):
    """docs/SCHEMA_DRAFT.md `memberships` table. Composite primary key
    `(circle_id, user_id)`, deliberately no separate `id` column —
    `contracts/chat/circles.py::Membership` has no `id` field on the wire,
    so storage shouldn't invent one the API never exposes."""

    __tablename__ = "memberships"
    __table_args__ = (
        sa.CheckConstraint(
            "role IN ('member', 'moderator', 'admin')", name="ck_memberships_role"
        ),
    )

    circle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("circles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'member'")
    )
    joined_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Conversation(Base):
    """docs/SCHEMA_DRAFT.md design question #1a, option (ii): a real table
    for DM (two-party) conversation identity, instead of a deterministic
    UUIDv5 hash over the sorted pair. Canonical ordering (`user_a < user_b`)
    is enforced at the DB level so "two different ids for the same pair" is
    structurally impossible, rather than depending on every future writer
    sorting the pair identically forever."""

    __tablename__ = "conversations"
    __table_args__ = (
        sa.CheckConstraint("user_a < user_b", name="ck_conversations_canonical_order"),
        sa.UniqueConstraint("user_a", "user_b", name="uq_conversations_user_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    user_a: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    user_b: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class Message(Base):
    """docs/SCHEMA_DRAFT.md `messages` table."""

    __tablename__ = "messages"
    __table_args__ = (
        # `messages` Indexes #2 / Constraints — the idempotency guarantee
        # contracts/chat/README.md design decision #2 promises.
        sa.UniqueConstraint(
            "author_id", "client_msg_id", name="uq_messages_author_client_msg_id"
        ),
        sa.CheckConstraint("target_type IN ('user', 'circle')", name="ck_messages_target_type"),
        sa.CheckConstraint("kind IN ('text', 'voice')", name="ck_messages_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'held', 'blocked', 'failed')",
            name="ck_messages_status",
        ),
        # `messages` Constraints — mirrors MessageIn's model_validator
        # (contracts/chat/messages.py) at the storage layer, so a write
        # that bypasses the Pydantic boundary can't create a "voice message
        # with no audio" state.
        sa.CheckConstraint(
            "(kind = 'text' AND text IS NOT NULL) "
            "OR (kind = 'voice' AND original_media_ref IS NOT NULL)",
            name="ck_messages_kind_payload",
        ),
        # design question #1, option (b): exactly one of the two target FKs
        # is populated, matching target_type.
        sa.CheckConstraint(
            "num_nonnulls(target_user_id, target_circle_id) = 1",
            name="ck_messages_target_exactly_one",
        ),
        # design question #1a: ties conversation_id to target_circle_id for
        # circle messages, the one case a direct DB-level comparison is
        # possible (no equivalent constraint exists for the DM case — see
        # the schema doc for why conversation_id can't carry a real FK).
        sa.CheckConstraint(
            "target_type <> 'circle' OR conversation_id = target_circle_id",
            name="ck_messages_conversation_id_matches_circle",
        ),
        # `messages` Indexes #3 — partial, backs the sync query. Partial on
        # `deleted_at IS NULL` because design question #2 puts that filter
        # on every list/sync query anyway, so a soft-deleted row's index
        # entry can be dropped for good instead of kept for a filter to
        # discard on every future scan.
        sa.Index(
            "ix_messages_conversation_id_id_not_deleted",
            "conversation_id",
            "id",
            postgresql_where=sa.text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    client_msg_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT")
    )
    target_circle_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("circles.id", ondelete="RESTRICT")
    )
    # The sync/storage grouping key — not the same thing as target_id on
    # the wire. No FK: a single column can't REFERENCES two different
    # tables conditionally on target_type in plain Postgres. Correctness
    # for a DM comes from the service-layer write path (resolve-or-create
    # against `conversations` in the same transaction as the insert), not
    # a declarative constraint — see design question #1a.
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    text: Mapped[str | None] = mapped_column(sa.Text)
    pivot_text_en: Mapped[str | None] = mapped_column(sa.Text)
    original_media_ref: Mapped[str | None] = mapped_column(sa.Text)
    media_duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    source_lang: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'")
    )
    # Set once at insert to created_at + the current undo-window policy;
    # never mutated afterward. Nothing reads this yet — design question #7,
    # for Week 4. NULL for message types/paths that don't support undo.
    undo_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
