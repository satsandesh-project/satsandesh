"""add admin_actions: the append-only log of organisation-admin actions

Revision ID: e5b8a3c71d94
Revises: d3a7c91e5b20
Create Date: 2026-10-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b8a3c71d94"
down_revision: str | Sequence[str] | None = "d3a7c91e5b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Same guard as moderation_events (c696e9f74472), with its own function so the
# two tables can be dropped independently: UPDATE is refused unconditionally;
# DELETE / TRUNCATE only inside a transaction that opted in with
# `SET LOCAL app.allow_audit_purge = 'on'` (one setting for every audit table,
# so the test fixtures' cleanup and a person-erasure request need only one).
_FUNCTION = """
CREATE FUNCTION admin_actions_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE')
       AND current_setting('app.allow_audit_purge', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'admin_actions is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql
"""

# Mirrors contracts/chat/admin_org.py::AdminActionKind.
_ACTIONS = (
    "'user.create', 'user.set_role', 'user.reissue_token', 'circle.create', "
    "'circle.rename', 'member.add', 'member.set_role', 'member.remove', "
    "'announcement.publish', 'admin.promote'"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("admin_id", sa.UUID(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"action IN ({_ACTIONS})", name="ck_admin_actions_action"),
        sa.CheckConstraint(
            "target_type IN ('user', 'circle', 'all_circles')",
            name="ck_admin_actions_target_type",
        ),
        # 'all_circles' (an announcement to every announcement circle) names no
        # single target; every other action names exactly one.
        sa.CheckConstraint(
            "(target_type = 'all_circles') = (target_id IS NULL)",
            name="ck_admin_actions_target_id_iff_single_target",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_admin_actions_details_object"
        ),
        # RESTRICT, not CASCADE: an audit log must outlive the account it records
        # (users are never deleted anyway; this keeps it true if that changes).
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The console reads it newest first, keyset-paged on (created_at, id).
    op.create_index("ix_admin_actions_created_at_id", "admin_actions", ["created_at", "id"])

    op.execute(_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_admin_actions_no_row_change "
        "BEFORE UPDATE OR DELETE ON admin_actions "
        "FOR EACH ROW EXECUTE FUNCTION admin_actions_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_admin_actions_no_truncate "
        "BEFORE TRUNCATE ON admin_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION admin_actions_append_only()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_admin_actions_no_truncate ON admin_actions")
    op.execute("DROP TRIGGER trg_admin_actions_no_row_change ON admin_actions")
    op.execute("DROP FUNCTION admin_actions_append_only()")
    op.drop_index("ix_admin_actions_created_at_id", table_name="admin_actions")
    op.drop_table("admin_actions")
