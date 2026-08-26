"""widen messages status CHECK for undo (sent, cancelled)

Revision ID: ee7195a99a19
Revises: 79fabea924d5
Create Date: 2026-08-26 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ee7195a99a19"
down_revision: str | Sequence[str] | None = "79fabea924d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Named constraint, so the drop is precise (app/db/models.py's
    # ck_messages_status) — not touching any other CHECK on this table.
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('pending', 'delivered', 'held', 'blocked', 'failed', 'sent', 'cancelled')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_messages_status", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_status",
        "messages",
        "status IN ('pending', 'delivered', 'held', 'blocked', 'failed')",
    )
