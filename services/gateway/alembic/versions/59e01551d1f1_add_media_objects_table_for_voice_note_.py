"""add media_objects table for voice-note storage

Revision ID: 59e01551d1f1
Revises: 67bf10861096
Create Date: 2026-09-22 08:42:47.164197

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "59e01551d1f1"
down_revision: str | Sequence[str] | None = "67bf10861096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_objects",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("sha256_hex", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "format IN ('webm_opus', 'ogg_opus', 'wav_pcm16', 'mp3')", name="ck_media_format"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_id", "sha256_hex", name="uq_media_author_sha256"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("media_objects")
