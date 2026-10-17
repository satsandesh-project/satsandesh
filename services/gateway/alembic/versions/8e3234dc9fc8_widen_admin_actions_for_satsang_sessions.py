"""widen admin_actions action and target_type CHECKs for satsang sessions

Revision ID: 8e3234dc9fc8
Revises: bc32cd718b80
Create Date: 2026-10-17 00:00:00.000000

`contracts/chat/admin_org.py` added four `AdminActionKind` values in contracts
0.11.0 -- `session.create`, `session.cancel`, `session.rotate_key`,
`session.end` -- and "session" as a target type, so the satsang routes
(bc32cd718b80's `sessions` table) can be audited like the other admin routes.
The gateway's `ADMIN_ACTIONS` and `TARGET_TYPES` carry them, so a NEW database
gets them from app/db/admin_actions.py's own CheckConstraints; a database that
already ran f1a7c3e92b05 holds the old lists and would refuse the INSERT. This
widens both.

The lists are spelled out rather than imported from the app: a migration has to
keep describing the schema as it was at this revision, whatever the code says
later.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e3234dc9fc8"
down_revision: str | Sequence[str] | None = "bc32cd718b80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS_BEFORE = (
    "user.create",
    "user.set_role",
    "user.reissue_token",
    "user.issue_claim",
    "circle.create",
    "circle.rename",
    "member.add",
    "member.set_role",
    "member.remove",
    "announcement.publish",
    "admin.promote",
)
_ACTIONS_AFTER = (
    *_ACTIONS_BEFORE,
    "session.create",
    "session.cancel",
    "session.rotate_key",
    "session.end",
)
_TARGETS_BEFORE = ("user", "circle", "all_circles")
_TARGETS_AFTER = (*_TARGETS_BEFORE, "session")


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace(name: str, column: str, values: Sequence[str]) -> None:
    # Named constraints, so the drop is precise: the table's other CHECKs
    # (target_id-iff-single-target, details-is-an-object) and its append-only
    # triggers are untouched.
    op.drop_constraint(name, "admin_actions", type_="check")
    op.create_check_constraint(name, "admin_actions", f"{column} IN ({_in_list(values)})")


def upgrade() -> None:
    """Upgrade schema."""
    _replace("ck_admin_actions_action", "action", _ACTIONS_AFTER)
    _replace("ck_admin_actions_target_type", "target_type", _TARGETS_AFTER)


def downgrade() -> None:
    """Downgrade schema."""
    # A row already logged for a session would fail the narrower lists, and the
    # log is append-only, so it cannot be removed to make room. Downgrading a
    # database that has audited a satsang therefore fails loudly here rather
    # than silently dropping the rule.
    _replace("ck_admin_actions_target_type", "target_type", _TARGETS_BEFORE)
    _replace("ck_admin_actions_action", "action", _ACTIONS_BEFORE)
