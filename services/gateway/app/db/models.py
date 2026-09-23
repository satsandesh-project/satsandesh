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
from datetime import datetime, time

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
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
    tts_on: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("true"))
    role: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'elder'"))
    # IANA zone name (e.g. "Asia/Kolkata"), never a UTC offset — design
    # question #4. No DB-level format constraint (Postgres has no built-in
    # domain for it); validate at the application layer later.
    timezone: Mapped[str | None] = mapped_column(sa.Text)
    # Week 3 Phase 7: wall-clock time-of-day only, no UTC offset attached —
    # interpreted against `timezone` above at push-check time (via
    # zoneinfo), not stored pre-converted to UTC, so the window stays
    # correct across DST transitions the same way design question #4
    # already reasons about for `timezone` itself. NULL means "not set by
    # this user yet"; the application-layer quiet-hours check falls back
    # to a fixed default window in that case — not a DB server_default,
    # since "unset" and "explicitly set to the default" are meaningfully
    # different states worth keeping distinguishable.
    quiet_hours_start: Mapped[time | None] = mapped_column(sa.Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(sa.Time)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Circle(Base):
    """docs/SCHEMA_DRAFT.md `circles` table."""

    __tablename__ = "circles"
    __table_args__ = (
        # 'group' (any member posts) vs 'announcement' (moderator/admin-only
        # posting, everyone reads) -- contracts/chat/circles.py::CircleKind.
        # server_default keeps every pre-existing row a plain group circle,
        # matching its actual behavior before this column existed.
        sa.CheckConstraint("kind IN ('group', 'announcement')", name="ck_circles_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'group'"))
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
        sa.CheckConstraint("role IN ('member', 'moderator', 'admin')", name="ck_memberships_role"),
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
    role: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'member'"))
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
        sa.UniqueConstraint("author_id", "client_msg_id", name="uq_messages_author_client_msg_id"),
        sa.CheckConstraint("target_type IN ('user', 'circle')", name="ck_messages_target_type"),
        sa.CheckConstraint("kind IN ('text', 'voice')", name="ck_messages_kind"),
        # Week 4 Phase 8: 'sent' and 'cancelled' added for the 30-second
        # undo window (see app/undo.py) — 'sent' marks a message that
        # cleared the window and fanned out for real, 'cancelled' one the
        # author undid within it. Matches contracts/chat/common.py's
        # MessageStatus enum exactly, same as every other value here.
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'held', 'blocked', 'failed', 'sent', 'cancelled')",
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


class MessageDelivery(Base):
    """Per-recipient delivery confirmation for a CIRCLE message -- the
    N-recipient counterpart to `messages.status`'s single-value
    'delivered' transition, which only ever meant one well-defined DM
    recipient (see app/ws.py's `_handle_message_delivered`) and doesn't
    translate to a message with multiple recipients. Raised as an open
    question on PR #16 once circles became a real, working feature (#32):
    what "delivered" even means for N people is a product decision, not
    an implementation one -- the one made here is an aggregate count
    (delivered_count / member_count), not "first confirms" or "all must
    confirm," since a partial count is still genuinely useful information
    and doesn't force the UI to wait on the slowest or laggiest member.

    Deliberately a separate table, not a mutation of `messages.status`:
    that column stays the sender-side pending/sent/cancelled lifecycle
    for every message, circle or DM, unchanged by this feature -- setting
    it to 'delivered' for a circle message would misrepresent a partial
    count as if the message were fully delivered to everyone.

    Composite PK (message_id, user_id): idempotent by construction, same
    reasoning as `messages`'s own client_msg_id uniqueness -- a duplicate
    ack from the same member (a retry, or a second device) hits the PK
    conflict and is a no-op, not a second row or a double count.
    """

    __tablename__ = "message_deliveries"

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("messages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    delivered_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class MediaObject(Base):
    """Week 6: a stored voice-note upload -- backs `contracts/chat/media.py`'s
    `MediaUploadOut` and `contracts/chat/common.py`'s `MediaRef.format`
    (see that file for the four allowed values, mirrored here in
    `ck_media_format`). One row per distinct uploaded file; the actual
    bytes live in whatever `app/media_storage.py`'s `MediaStorage`
    interface backs (local disk for now -- see app/media_storage.py's own
    module docstring) at a path derived purely from `id`, never stored as
    a separate column here, so there's exactly one source of truth for
    where a given object's bytes are.

    Not yet referenced by a foreign key from `messages` -- `messages.
    original_media_ref` (this table's precursor, still just a plain text
    URI column) is unchanged in this phase; wiring `app/messages.py`'s
    routes to this table is separate, later work, not this phase's
    (storage only).

    `(author_id, sha256_hex)` is the idempotency key: the SAME author
    re-uploading the SAME bytes (a retry after a dropped ack -- expected
    to happen constantly on the elder pilot's target networks, same
    reasoning as `messages`'s own `(author_id, client_msg_id)`
    uniqueness) must get back the SAME row, not a second copy. Content-
    hash-based, not a client-supplied key, because `POST /media`
    (contracts/chat/README.md) has no id-like parameter for a client to
    make one up with -- the bytes themselves are the only thing that's
    the same across a retry."""

    __tablename__ = "media_objects"
    __table_args__ = (
        sa.UniqueConstraint("author_id", "sha256_hex", name="uq_media_author_sha256"),
        sa.CheckConstraint(
            "format IN ('webm_opus', 'ogg_opus', 'wav_pcm16', 'mp3')",
            name="ck_media_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # RESTRICT, not CASCADE -- same reasoning as messages.author_id
        # (module docstring): a stored voice note is part of message
        # history, an audit trail that should survive the uploader's own
        # user row being removed, not owned-and-meaningless-without-them
        # the way PushSubscription/Invite are.
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    format: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sha256_hex: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class PushSubscription(Base):
    """Week 3 Phase 7: one row per browser/device Web Push subscription. A
    user can have several (one per device/browser they've enabled push
    on) — `endpoint` (the browser's own push endpoint URL) is the natural
    dedup key, not `user_id`, since the Push API guarantees an endpoint is
    unique per subscription."""

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # CASCADE, not RESTRICT — a deliberate deviation from this file's
        # dominant FK-to-users.id pattern (see the module docstring: every
        # FK to users.id is RESTRICT except memberships.circle_id, which
        # cascades because a membership can't outlive its circle). A push
        # subscription is the same shape of dependency: it's owned by, and
        # meaningless without, its user — unlike messages.author_id, which
        # RESTRICTs specifically to preserve message history as an audit
        # trail after a user is gone. Confirmed Week 3 Phase 7 Step 0.
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(sa.Text, nullable=False)
    auth: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class Invite(Base):
    """Family-assisted QR onboarding invite (see app/onboarding.py's module
    docstring for the full invite -> QR -> activate flow).

    Replaces onboarding._pending, the in-memory dict PR #29's review
    (kpspyolo024) flagged as unsafe: a killed-and-restarted gateway
    silently dropped every pending invite, including ones already handed
    to an elder as a printed/shown QR code. Moving storage here means an
    invite survives a restart, and — since `activate_invite` now consumes
    a row via a single DELETE ... RETURNING rather than a Python dict.pop
    — stays single-use even across multiple gateway instances, which the
    in-memory version could never guarantee to begin with.

    `id` is the invite_id embedded in the signed token (see
    onboarding._issue_invite_token), not a UUID7 like every other table
    here — it's already unique and unguessable by construction (32 random
    bytes via secrets.token_urlsafe), so reusing it as the primary key
    avoids a second lookup key.
    """

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    phone: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="")
    language: Mapped[str] = mapped_column(sa.Text, nullable=False)
    invited_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        # CASCADE, not RESTRICT — same reasoning as PushSubscription.user_id
        # above: an invite is owned by, and meaningless without, the family
        # member who created it, unlike messages.author_id's audit-trail
        # RESTRICT.
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)


class Job(Base):
    """Week 6: a durable, polled job queue -- a table, not
    app/undo.py's `dict[str, asyncio.Task]` (see that file's own
    docstring: in-memory, single-process only, everything pending lost on
    restart or invisible to a second gateway process). A row here
    survives both.

    `status` lifecycle: queued -> running -> done, or queued -> running ->
    queued (retry, under max_attempts) -> ... -> dead (exhausted).
    `claimed_by`/`lease_expires_at` are how a worker takes ownership --
    app/db/repository.py's claim_next_job atomically moves a row from
    queued to running with a single conditional UPDATE ... WHERE ...
    RETURNING (same idiom as set_message_status below), never a
    SELECT ... FOR UPDATE (not used anywhere in this codebase) and
    never a bare `UPDATE ... WHERE id = (SELECT id FROM jobs WHERE
    <condition> LIMIT 1)` -- that subquery form is not safe under
    concurrent claims: Postgres's EvalPlanQual re-check on a lock
    conflict only re-validates the outer `WHERE id = X`, not the
    condition baked into the subquery, so two workers can both match the
    same subquery result before either commits. Keeping the full
    condition (status='queued' OR lease expired) directly in the
    UPDATE's own WHERE clause is what makes the second worker's UPDATE
    affect zero rows once the first one commits.

    `attempts` is incremented by claim_next_job itself, in the same
    committed UPDATE that claims the row -- not by the job handler, and
    not in whatever transaction the handler's own work uses. That's the
    fix for the recurring bug class services/auth/DECISIONS.md D11
    records (services/auth/ doesn't exist in this repo, checked -- the
    bug class is real regardless): a counter incremented inside a
    transaction that then fails is rolled back with it, so a poisoned job
    would retry forever without ever being counted toward
    max_attempts. See tests/test_job_queue.py's
    test_rollback_test_attempts_survives_a_failed_jobs_transaction for the
    regression test proving this.

    `payload` is JSONB, not a second table per job_type -- this queue is
    deliberately generic (any job_type, any JSON-serializable payload),
    matching the wire contract of nothing here being client-facing (no
    contracts/ file defines this shape; it's purely an internal
    implementation detail of app/jobs.py's worker loop)."""

    __tablename__ = "jobs"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'dead')", name="ck_jobs_status"
        ),
        # claim_next_job's candidate SELECT filters on status plus
        # next_attempt_at (the common 'queued' branch) and orders by
        # next_attempt_at -- this index is what keeps that from becoming a
        # full table scan as the queue grows.
        sa.Index("ix_jobs_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    job_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default=sa.text("'queued'"))
    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("5")
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text)
    claimed_by: Mapped[str | None] = mapped_column(sa.Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # When this job becomes eligible to be claimed again -- set to now() on
    # enqueue, pushed forward by claim_next_job's backoff calculation each
    # time fail_job records a retryable failure. A queued job whose
    # next_attempt_at is still in the future is deliberately NOT claimed,
    # even though its status is 'queued' -- this is the backoff delay, not
    # a second status value, because "queued but not yet due" and "queued
    # and due" both still mean queued from every other angle (counting,
    # listing, dead-lettering).
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
