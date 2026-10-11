"""widen admin_actions action CHECK for user.issue_claim

Revision ID: f1a7c3e92b05
Revises: e5b8a3c71d94
Create Date: 2026-10-11 00:00:00.000000

`contracts/chat/admin_org.py` added `AdminActionKind.USER_ISSUE_CLAIM`
("user.issue_claim") in contracts 0.8.0 (#141, the single-use sign-in claim).
The gateway's `ADMIN_ACTIONS` now carries it, so a NEW database gets it from
app/db/admin_actions.py's own CheckConstraint -- but a database that already
ran e5b8a3c71d94 holds the old list and refuses the INSERT. This widens it.

The row's own list is deliberately spelled out rather than imported from the
app: a migration has to keep describing the schema as it was at this revision,
whatever the code says later.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a7c3e92b05"
down_revision: str | Sequence[str] | None = "e5b8a3c71d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = (
    "user.create",
    "user.set_role",
    "user.reissue_token",
    "circle.create",
    "circle.rename",
    "member.add",
    "member.set_role",
    "member.remove",
    "announcement.publish",
    "admin.promote",
)
_AFTER = (*_BEFORE[:3], "user.issue_claim", *_BEFORE[3:])


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    """Upgrade schema."""
    # Named constraint, so the drop is precise: the table's three other CHECKs
    # (target_type, target_id-iff-single-target, details-is-an-object) and its
    # append-only triggers are untouched.
    op.drop_constraint("ck_admin_actions_action", "admin_actions", type_="check")
    op.create_check_constraint(
        "ck_admin_actions_action", "admin_actions", f"action IN ({_in_list(_AFTER)})"
    )


def downgrade() -> None:
    """Downgrade schema."""
    # A row already logged with the new name would fail this; the log is
    # append-only, so such a row cannot be removed to make room for the
    # narrower constraint. Downgrading a database that has issued a claim
    # therefore fails loudly here rather than silently dropping the rule.
    op.drop_constraint("ck_admin_actions_action", "admin_actions", type_="check")
    op.create_check_constraint(
        "ck_admin_actions_action", "admin_actions", f"action IN ({_in_list(_BEFORE)})"
    )
