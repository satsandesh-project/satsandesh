"""add messages.pipeline_state

Revision ID: b9d4f1a27c3e
Revises: c8308451fe81
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d4f1a27c3e"
down_revision: str | Sequence[str] | None = "c8308451fe81"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # NULL for every existing row = "no pipeline", i.e. exactly today's
    # behaviour; safe on a populated table.
    op.add_column("messages", sa.Column("pipeline_state", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_messages_pipeline_state",
        "messages",
        "pipeline_state IS NULL OR pipeline_state IN ('pending', 'complete', 'failed')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_messages_pipeline_state", "messages", type_="check")
    op.drop_column("messages", "pipeline_state")
