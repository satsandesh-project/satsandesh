"""The `sessions` table: the scheduled satsang broadcast (contracts/chat/satsang.py).

A separate module, not more of app/db/models.py, so the feature owns its own storage. Anything
that builds the schema from `Base.metadata` (the test fixtures' cleanup) sees this table once this
module is imported -- the routes that use it (app/satsang.py) import it.

Two rules the database holds on its own, so a bug in a route cannot break them:

- **It cannot hold a stream key.** Only `stream_key_sha256` is stored, a lowercase 64-hex SHA-256
  digest that a CHECK insists on. The key itself exists in exactly one API response, when a
  session is created or its key rotated. OPEN_QUESTIONS #25 (a Week 7 read path that returned
  every message not yet out) happened because a read path returned something it HELD; this table
  holds nothing worth returning. A plain SHA-256 is right, and a slow KDF is not: the key is 256
  random bits (`secrets.token_urlsafe(32)`), not a human password, and the ingest hook looks a
  session up by hash.
- **At most one session is live.** A partial unique index on `status` where it is 'live', so two
  requests racing to go on air cannot both win. This is global because a satsang is one-to-many
  to the whole community (proposal section 7.4), not scoped to a circle.

- **Only legal moves.** A trigger (created by the migration; SQLAlchemy cannot declare one)
  refuses any change of `status` other than scheduled->live, scheduled->cancelled and
  live->ended. The CHECKs hold a status and its timestamps consistent, but ended->live is
  consistent too once `ended_at` is cleared, so something has to say which MOVES are allowed.

What is NOT here, because it compares against the clock and a CHECK needs an immutable
expression: "a session may go live from 60 minutes before its start". That is the gateway's rule,
and its test.

`status` mirrors `contracts/chat/satsang.py::SatsangStatus`; a test pins the two together.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.id import generate_uuid7

SATSANG_STATUSES = ("scheduled", "live", "ended", "cancelled")


class SatsangSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('scheduled', 'live', 'ended', 'cancelled')", name="ck_sessions_status"
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="ck_sessions_title_length"),
        # A SHA-256 hex digest and nothing else: a plaintext key pasted into this column by mistake
        # is refused rather than stored.
        sa.CheckConstraint(
            "stream_key_sha256 ~ '^[0-9a-f]{64}$'", name="ck_sessions_key_hash_shape"
        ),
        # It has started exactly when it is live or ended; cancelled never started.
        sa.CheckConstraint(
            "(status IN ('live', 'ended')) = (started_at IS NOT NULL)",
            name="ck_sessions_started_at_iff_started",
        ),
        sa.CheckConstraint(
            "(status = 'ended') = (ended_at IS NOT NULL)", name="ck_sessions_ended_at_iff_ended"
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ck_sessions_end_after_start",
        ),
        sa.UniqueConstraint("stream_key_sha256", name="uq_sessions_stream_key_sha256"),
        # Exactly one microphone on the air: never two live sessions, enforced by the database.
        sa.Index(
            "uq_sessions_one_live",
            "status",
            unique=True,
            postgresql_where=sa.text("status = 'live'"),
        ),
        # The elder listing and the console both read by status, soonest first.
        sa.Index("ix_sessions_status_scheduled_start_at", "status", "scheduled_start_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # RESTRICT, as `rooms.created_by` and `circles.created_by`: the schedule is a record of what
    # was broadcast, not the speaker's own data to be cascaded away with them.
    speaker_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    scheduled_start_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="scheduled")
    stream_key_sha256: Mapped[str] = mapped_column(sa.Text, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    # A timestamp, not a boolean: it answers WHEN, survives a crash between sending and committing
    # informatively, and tied to no status because a reminder followed by a cancellation is
    # ordinary.
    reminder_sent_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class SatsangReminderRendering(Base):
    """One language's version of a satsang's reminder: the text a member reads and, when speech was
    made, its audio. The same shape as `message_renderings`, keyed by the satsang instead of a message.

    Primary key `(session_id, language)`: one per language. `language` is the language a RECIPIENT
    reads (the key a reader is looked up by), and `translated` says whether `text` really is in it:
    False means `text` is the English source, because the language could not be rendered (the render
    service was down, or it is a language the pipeline cannot render). A reader never sees a row
    labelled Telugu that holds English.

    `session_id` CASCADEs (derived data, meaningless without its satsang). `audio_media_object_id` is
    ON DELETE SET NULL for the same reason as `message_renderings`: retention sweeps the audio after
    MEDIA_RETENTION_DAYS and the words outlive it. `model_version_*` are audit data, not for a reader."""

    __tablename__ = "satsang_reminder_renderings"
    __table_args__ = (
        sa.CheckConstraint("length(text) > 0", name="ck_satsang_reminder_renderings_text_nonempty"),
        sa.CheckConstraint(
            "language ~ '^[a-z]{2,3}$'", name="ck_satsang_reminder_renderings_language"
        ),
        sa.CheckConstraint(
            "degraded_reason IS NULL OR degraded_reason IN "
            "('text_only', 'tts_skipped', 'model_fallback', 'rate_limited')",
            name="ck_satsang_reminder_renderings_degraded_reason",
        ),
        # A reason that says "no audio" must not carry audio (message_renderings' rule, restated).
        sa.CheckConstraint(
            "degraded_reason IS NULL OR degraded_reason NOT IN ('text_only', 'tts_skipped') "
            "OR audio_media_object_id IS NULL",
            name="ck_satsang_reminder_renderings_no_audio_reasons",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    translated: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    audio_media_object_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("media_objects.id", ondelete="SET NULL")
    )
    degraded_reason: Mapped[str | None] = mapped_column(sa.Text)
    model_version_translate: Mapped[str | None] = mapped_column(sa.Text)
    model_version_tts: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
