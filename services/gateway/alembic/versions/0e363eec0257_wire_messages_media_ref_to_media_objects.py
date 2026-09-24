"""wire messages.media_ref to media_objects

Revision ID: 0e363eec0257
Revises: 1e85689594ec
Create Date: 2026-09-24 00:00:00.000000

Adds messages.media_object_id (nullable FK -> media_objects.id, ON
DELETE SET NULL) and messages.media_format (nullable, mirrors
media_objects.format's own allowed values). original_media_ref and
media_duration_ms are untouched -- this is purely additive.

Backfill is best-effort and non-destructive: an existing voice message's
media_object_id/media_format get set ONLY when original_media_ref
parses as "media:<uuid>", that uuid exists in media_objects, AND the
message's own author_id matches that row's author_id. Everything else
(malformed, non-existent, wrong-owner references) is left NULL, which is
the honest state for data this migration cannot verify -- not an error,
and not something this migration rejects or fails on.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0e363eec0257"
down_revision: str | Sequence[str] | None = "1e85689594ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("messages", sa.Column("media_object_id", sa.UUID(), nullable=True))
    op.add_column("messages", sa.Column("media_format", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_messages_media_object_id_media_objects",
        "messages",
        "media_objects",
        ["media_object_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_messages_media_format",
        "messages",
        "media_format IS NULL OR media_format IN ('webm_opus', 'ogg_opus', 'wav_pcm16', 'mp3')",
    )

    # Best-effort backfill -- see module docstring for exactly what does
    # and does not get linked. 'media:' is the only scheme this deployment
    # can verify (app/media.py); substring(original_media_ref from 7) is
    # everything after that 6-character prefix + colon.
    op.execute(
        """
        UPDATE messages
        SET media_object_id = mo.id,
            media_format = mo.format
        FROM media_objects mo
        WHERE messages.kind = 'voice'
          AND messages.original_media_ref LIKE 'media:%'
          AND messages.author_id = mo.author_id
          AND substring(messages.original_media_ref from 7) = mo.id::text
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_messages_media_format", "messages", type_="check")
    op.drop_constraint("fk_messages_media_object_id_media_objects", "messages", type_="foreignkey")
    op.drop_column("messages", "media_format")
    op.drop_column("messages", "media_object_id")
