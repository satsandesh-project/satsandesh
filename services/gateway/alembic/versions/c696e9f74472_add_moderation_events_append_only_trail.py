"""add moderation_events: the append-only moderation trail

Revision ID: c696e9f74472
Revises: 0e363eec0257
Create Date: 2026-10-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c696e9f74472"
down_revision: str | Sequence[str] | None = "0e363eec0257"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Static DDL, no interpolated values. UPDATE is refused unconditionally.
# DELETE / TRUNCATE are refused unless the transaction opted in with
# `SET LOCAL app.allow_audit_purge = 'on'` -- the one deliberate purge path
# (erasing a person's data on request; the test fixtures' cleanup). A
# transaction-local setting, so it cannot leak to the next transaction.
_FUNCTION = """
CREATE FUNCTION moderation_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE')
       AND current_setting('app.allow_audit_purge', true) = 'on' THEN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'moderation_events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "moderation_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("notice_text", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("degraded_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_kind IN ('classifier', 'moderator', 'system')",
            name="ck_moderation_events_actor_kind",
        ),
        sa.CheckConstraint(
            "label IN ('A_DEVOTIONAL', 'B_ORGANIZATIONAL', 'C_PERSONAL', "
            "'D_DISPUTATIONAL', 'E_HARMFUL')",
            name="ck_moderation_events_label",
        ),
        sa.CheckConstraint(
            "action IN ('ALLOW', 'NUDGE', 'HOLD', 'BLOCK')",
            name="ck_moderation_events_action",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_moderation_events_confidence_range",
        ),
        sa.CheckConstraint(
            "(actor_kind = 'moderator') = (actor_id IS NOT NULL)",
            name="ck_moderation_events_actor_id_iff_moderator",
        ),
        sa.CheckConstraint(
            "actor_kind = 'classifier' OR (confidence IS NULL AND model_version IS NULL)",
            name="ck_moderation_events_only_classifier_has_model_opinion",
        ),
        sa.CheckConstraint(
            "NOT degraded OR actor_kind = 'classifier'",
            name="ck_moderation_events_degraded_only_classifier",
        ),
        sa.CheckConstraint(
            "degraded_reason IS NULL OR (degraded AND degraded_reason IN "
            "('text_only', 'tts_skipped', 'model_fallback', 'rate_limited'))",
            name="ck_moderation_events_degraded_reason",
        ),
        sa.CheckConstraint(
            "note IS NULL OR length(note) <= 2000", name="ck_moderation_events_note_length"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moderation_events_message_id_created_at_id",
        "moderation_events",
        ["message_id", "created_at", "id"],
    )

    op.execute(_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_moderation_events_no_row_change "
        "BEFORE UPDATE OR DELETE ON moderation_events "
        "FOR EACH ROW EXECUTE FUNCTION moderation_events_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_moderation_events_no_truncate "
        "BEFORE TRUNCATE ON moderation_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION moderation_events_append_only()"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_moderation_events_no_truncate ON moderation_events")
    op.execute("DROP TRIGGER trg_moderation_events_no_row_change ON moderation_events")
    op.execute("DROP FUNCTION moderation_events_append_only()")
    op.drop_index("ix_moderation_events_message_id_created_at_id", table_name="moderation_events")
    op.drop_table("moderation_events")
