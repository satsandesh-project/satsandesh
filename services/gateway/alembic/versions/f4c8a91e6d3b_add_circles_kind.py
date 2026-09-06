"""add circles.kind (group vs announcement)

Revision ID: f4c8a91e6d3b
Revises: a3d9f0c1b2e4
Create Date: 2026-09-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4c8a91e6d3b"
down_revision: str | Sequence[str] | None = "a3d9f0c1b2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "circles",
        sa.Column("kind", sa.Text(), nullable=False, server_default=sa.text("'group'")),
    )
    op.create_check_constraint(
        "ck_circles_kind",
        "circles",
        "kind IN ('group', 'announcement')",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_circles_kind", "circles", type_="check")
    op.drop_column("circles", "kind")
