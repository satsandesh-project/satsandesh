"""add satsang_reminder_deliveries: who has been pushed a satsang's reminder

Revision ID: c8f2a6d41e93
Revises: d3a9f1c27b58
Create Date: 2026-10-16 00:00:00.000000

One row per (satsang, user): the ledger that lets the reminder be sent without holding the satsang row
locked, or a transaction open, while the push calls are made (OPEN_QUESTIONS #49). Follows
d3a9f1c27b58 (the reminder renderings); there is still one head.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f2a6d41e93"
down_revision: str | Sequence[str] | None = "d3a9f1c27b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "satsang_reminder_deliveries",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "user_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("satsang_reminder_deliveries")
