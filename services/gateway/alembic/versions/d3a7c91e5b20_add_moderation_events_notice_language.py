"""add moderation_events.notice_language

Revision ID: d3a7c91e5b20
Revises: b9d4f1a27c3e
Create Date: 2026-10-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3a7c91e5b20"
down_revision: str | Sequence[str] | None = "b9d4f1a27c3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # The language `notice_text` was written in, recorded with it: the wire's
    # `moderation_notice_language` must be what the sender was actually told in, not
    # whatever their language setting says by the time they read it. NULL for every
    # existing row (and for any event without a notice). A plain ADD COLUMN: the
    # append-only trigger guards UPDATE/DELETE/TRUNCATE, not DDL, and nothing is rewritten.
    op.add_column("moderation_events", sa.Column("notice_language", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("moderation_events", "notice_language")
