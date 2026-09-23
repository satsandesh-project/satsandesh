"""add jobs table for durable job queue

Revision ID: 1e85689594ec
Revises: 59e01551d1f1
Create Date: 2026-09-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1e85689594ec"
down_revision: str | Sequence[str] | None = "59e01551d1f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'dead')", name="ck_jobs_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # claim_next_job's candidate SELECT filters on status plus one of
    # next_attempt_at/lease_expires_at depending on branch, then orders by
    # next_attempt_at -- this covers the 'queued' branch (the common case)
    # without a full table scan as the queue grows.
    op.create_index(
        "ix_jobs_status_next_attempt_at", "jobs", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_jobs_status_next_attempt_at", table_name="jobs")
    op.drop_table("jobs")
