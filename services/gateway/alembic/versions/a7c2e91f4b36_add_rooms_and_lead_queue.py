"""add rooms and lead_queue: bhajan rooms with floor control

Revision ID: a7c2e91f4b36
Revises: e5b8a3c71d94
Create Date: 2026-10-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2e91f4b36"
down_revision: str | Sequence[str] | None = "e5b8a3c71d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "rooms",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("circle_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("livekit_room", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_rooms_status"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="ck_rooms_title_length"),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name="ck_rooms_closed_at_iff_closed"
        ),
        sa.ForeignKeyConstraint(["circle_id"], ["circles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("livekit_room"),
    )
    op.create_index("ix_rooms_circle_id_status", "rooms", ["circle_id", "status"])

    op.create_table(
        "lead_queue",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("room_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), server_default="waiting", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('waiting', 'leading', 'done', 'removed')", name="ck_lead_queue_state"
        ),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Exactly one microphone: never two leads in one room, enforced by the database.
    op.create_index(
        "uq_lead_queue_one_lead",
        "lead_queue",
        ["room_id"],
        unique=True,
        postgresql_where=sa.text("state = 'leading'"),
    )
    # A person is in the queue at most once at a time.
    op.create_index(
        "uq_lead_queue_one_active_place",
        "lead_queue",
        ["room_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('waiting', 'leading')"),
    )
    op.create_index(
        "ix_lead_queue_room_state_position", "lead_queue", ["room_id", "state", "position"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("lead_queue")
    op.drop_table("rooms")
