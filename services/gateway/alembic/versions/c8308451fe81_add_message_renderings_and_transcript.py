"""add message_renderings and messages.transcript

Revision ID: c8308451fe81
Revises: c696e9f74472
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8308451fe81"
down_revision: str | Sequence[str] | None = "c696e9f74472"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The pipeline's transcript of a voice note. Nullable and unconstrained
    # for every existing row, so this is safe on a populated table.
    op.add_column("messages", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("transcript_language", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_messages_transcript_pair",
        "messages",
        "(transcript IS NULL) = (transcript_language IS NULL)",
    )
    op.create_check_constraint(
        "ck_messages_transcript_voice_only", "messages", "transcript IS NULL OR kind = 'voice'"
    )
    op.create_check_constraint(
        "ck_messages_transcript_nonempty",
        "messages",
        "transcript IS NULL OR length(transcript) > 0",
    )
    op.create_check_constraint(
        "ck_messages_transcript_language",
        "messages",
        "transcript_language IS NULL OR transcript_language ~ '^[a-z]{2,3}$'",
    )

    op.create_table(
        "message_renderings",
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
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
        sa.CheckConstraint("length(text) > 0", name="ck_message_renderings_text_nonempty"),
        sa.CheckConstraint("language ~ '^[a-z]{2,3}$'", name="ck_message_renderings_language"),
        sa.CheckConstraint(
            "degraded_reason IS NULL OR degraded_reason IN "
            "('text_only', 'tts_skipped', 'model_fallback', 'rate_limited')",
            name="ck_message_renderings_degraded_reason",
        ),
        sa.CheckConstraint(
            "degraded_reason IS NULL OR degraded_reason NOT IN ('text_only', 'tts_skipped') "
            "OR audio_media_object_id IS NULL",
            name="ck_message_renderings_no_audio_reasons",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["audio_media_object_id"], ["media_objects.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("message_id", "language"),
    )
    op.create_index(
        "ix_message_renderings_audio_media_object_id",
        "message_renderings",
        ["audio_media_object_id"],
        postgresql_where=sa.text("audio_media_object_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_message_renderings_audio_media_object_id", table_name="message_renderings")
    op.drop_table("message_renderings")
    op.drop_constraint("ck_messages_transcript_language", "messages", type_="check")
    op.drop_constraint("ck_messages_transcript_nonempty", "messages", type_="check")
    op.drop_constraint("ck_messages_transcript_voice_only", "messages", type_="check")
    op.drop_constraint("ck_messages_transcript_pair", "messages", type_="check")
    op.drop_column("messages", "transcript_language")
    op.drop_column("messages", "transcript")
