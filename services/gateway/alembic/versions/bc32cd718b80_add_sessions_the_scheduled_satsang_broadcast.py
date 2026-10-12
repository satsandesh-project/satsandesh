"""add sessions: the scheduled satsang broadcast

Revision ID: bc32cd718b80
Revises: a7c2e91f4b36
Create Date: 2026-10-12 00:00:00.000000

The table holds only a SHA-256 of the stream key, never the key (see app/db/satsang.py), and a
partial unique index lets at most one session be live at a time.

Follows a7c2e91f4b36 (bhajan rooms), which merged first: both were written against
f1a7c3e92b05, and two migrations with one parent are two heads, so the second to merge takes
the first as its parent.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bc32cd718b80"
down_revision: str | Sequence[str] | None = "a7c2e91f4b36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("speaker_user_id", sa.UUID(), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default="scheduled", nullable=False),
        sa.Column("stream_key_sha256", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'live', 'ended', 'cancelled')", name="ck_sessions_status"
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="ck_sessions_title_length"),
        sa.CheckConstraint(
            "stream_key_sha256 ~ '^[0-9a-f]{64}$'", name="ck_sessions_key_hash_shape"
        ),
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
        sa.ForeignKeyConstraint(["speaker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream_key_sha256", name="uq_sessions_stream_key_sha256"),
    )
    # Exactly one microphone on the air: never two live sessions, enforced by the database.
    op.create_index(
        "uq_sessions_one_live",
        "sessions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'live'"),
    )
    op.create_index(
        "ix_sessions_status_scheduled_start_at", "sessions", ["status", "scheduled_start_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sessions")
