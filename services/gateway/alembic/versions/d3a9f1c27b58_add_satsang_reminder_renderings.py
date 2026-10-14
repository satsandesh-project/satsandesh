"""add satsang_reminder_renderings: a satsang reminder, per language

Revision ID: d3a9f1c27b58
Revises: bc32cd718b80
Create Date: 2026-10-14 00:00:00.000000

One row per (satsang, language): the reminder's text in that language and, when speech was made, its
audio (a media object). Follows bc32cd718b80 (the sessions table), which it references; there is
still one head.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a9f1c27b58"
down_revision: str | Sequence[str] | None = "bc32cd718b80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "satsang_reminder_renderings",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("translated", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("audio_media_object_id", sa.UUID(), nullable=True),
        sa.Column("degraded_reason", sa.Text(), nullable=True),
        sa.Column("model_version_translate", sa.Text(), nullable=True),
        sa.Column("model_version_tts", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(text) > 0", name="ck_satsang_reminder_renderings_text_nonempty"),
        sa.CheckConstraint(
            "language ~ '^[a-z]{2,3}$'", name="ck_satsang_reminder_renderings_language"
        ),
        sa.CheckConstraint(
            "degraded_reason IS NULL OR degraded_reason IN "
            "('text_only', 'tts_skipped', 'model_fallback', 'rate_limited')",
            name="ck_satsang_reminder_renderings_degraded_reason",
        ),
        sa.CheckConstraint(
            "degraded_reason IS NULL OR degraded_reason NOT IN ('text_only', 'tts_skipped') "
            "OR audio_media_object_id IS NULL",
            name="ck_satsang_reminder_renderings_no_audio_reasons",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["audio_media_object_id"], ["media_objects.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("session_id", "language"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("satsang_reminder_renderings")
