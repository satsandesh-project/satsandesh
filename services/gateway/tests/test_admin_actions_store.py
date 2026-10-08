"""The `admin_actions` log (organisation admin): its constraints and -- the point
of an audit log -- that a written row cannot be changed or removed.

Runs against a migrated Postgres like the other store tests
(`alembic upgrade head`, `TEST_DATABASE_URL`). The single-head check at the
bottom needs no database.
"""

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.admin_actions import AdminAction
from app.db.models import User as DbUser


def _admin(db_session):
    user = DbUser(name="Asha", preferred_language="te", role="admin")
    db_session.add(user)
    db_session.flush()
    return user


def _action(db_session, admin=None, **overrides):
    admin = admin or _admin(db_session)
    fields = {
        "admin_id": admin.id,
        "action": "circle.create",
        "target_type": "circle",
        "target_id": uuid.uuid4(),
        "details": {"name": "Evening Satsang"},
    }
    fields.update(overrides)
    row = AdminAction(**fields)
    db_session.add(row)
    db_session.commit()
    return row


def _expect_blocked(db_session, sql, **params):
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text(sql), params)
    db_session.rollback()


def test_a_row_is_stored_with_its_defaults(db_session):
    row = _action(db_session, details={})
    db_session.expire_all()
    stored = db_session.get(AdminAction, row.id)
    assert stored.details == {} and stored.created_at is not None


def test_details_default_to_an_empty_object(db_session):
    admin = _admin(db_session)
    db_session.execute(
        text(
            "INSERT INTO admin_actions (id, admin_id, action, target_type, target_id) "
            "VALUES (:id, :admin, 'user.create', 'user', :target)"
        ),
        {"id": uuid.uuid4(), "admin": admin.id, "target": uuid.uuid4()},
    )
    db_session.commit()
    assert db_session.query(AdminAction).one().details == {}


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "user.delete"},
        {"target_type": "message"},
        {"details": ["not", "an", "object"]},
        {"target_type": "all_circles"},  # names a single target: must have none
        {"target_id": None},  # a single-target action with no target
    ],
)
def test_a_row_that_breaks_a_rule_is_refused(db_session, overrides):
    with pytest.raises(IntegrityError):
        _action(db_session, **overrides)
    db_session.rollback()


def test_an_announcement_to_all_circles_has_no_single_target(db_session):
    row = _action(
        db_session,
        action="announcement.publish",
        target_type="all_circles",
        target_id=None,
        details={"circle_ids": []},
    )
    assert row.target_id is None


def test_the_acting_admin_must_exist(db_session):
    ghost = DbUser(id=uuid.uuid4(), name="Ghost", preferred_language="te")
    with pytest.raises(IntegrityError):
        _action(db_session, admin=ghost)
    db_session.rollback()


def test_an_admin_with_a_logged_action_cannot_be_deleted(db_session):
    admin = _admin(db_session)
    _action(db_session, admin=admin)
    with pytest.raises(IntegrityError):
        db_session.delete(admin)
        db_session.commit()
    db_session.rollback()


def test_a_stored_row_cannot_be_updated(db_session):
    row = _action(db_session)
    _expect_blocked(
        db_session, "UPDATE admin_actions SET action = 'user.create' WHERE id = :id", id=row.id
    )


def test_a_stored_row_cannot_be_deleted(db_session):
    row = _action(db_session)
    _expect_blocked(db_session, "DELETE FROM admin_actions WHERE id = :id", id=row.id)


def test_the_log_cannot_be_truncated(db_session):
    _action(db_session)
    _expect_blocked(db_session, "TRUNCATE admin_actions")


def test_the_purge_opt_in_never_allows_an_update(db_session):
    row = _action(db_session)
    db_session.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
    _expect_blocked(
        db_session, "UPDATE admin_actions SET action = 'user.create' WHERE id = :id", id=row.id
    )


def test_the_purge_opt_in_is_explicit_and_transaction_local(db_session):
    first = _action(db_session)
    db_session.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
    db_session.execute(text("DELETE FROM admin_actions WHERE id = :id"), {"id": first.id})
    db_session.commit()
    assert db_session.query(AdminAction).count() == 0
    second = _action(db_session)
    _expect_blocked(db_session, "DELETE FROM admin_actions WHERE id = :id", id=second.id)


def test_the_append_only_triggers_exist(db_session):
    names = {
        row[0]
        for row in db_session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'admin_actions'::regclass AND NOT tgisinternal"
            )
        )
    }
    assert names == {"trg_admin_actions_no_row_change", "trg_admin_actions_no_truncate"}


# --- no database needed ---------------------------------------------------------------


def test_the_migration_chain_has_exactly_one_head():
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    revisions, parents = set(), set()
    for path in versions.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revisions.add(re.search(r'^revision: str = "(\w+)"', source, re.MULTILINE).group(1))
        parent = re.search(r'^down_revision: .*= (None|"(\w+)")', source, re.MULTILINE)
        if parent.group(2):
            parents.add(parent.group(2))
    assert len(revisions - parents) == 1, f"alembic heads: {sorted(revisions - parents)}"
