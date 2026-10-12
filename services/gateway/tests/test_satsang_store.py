"""The `sessions` table (the scheduled satsang broadcast): what the DATABASE enforces.

Runs against a migrated Postgres like the other store tests (`alembic upgrade head`,
`TEST_DATABASE_URL`). Every rule here is one the schema holds on its own, so a bug in the routes
cannot put the data in a state the contract (contracts/chat/satsang.py) says is impossible.

The two that matter most:

- **The database cannot hold a stream key.** Only its SHA-256 is stored, so no read path can leak
  one, because there is nothing to leak (OPEN_QUESTIONS #25 was a read path returning something it
  held). Asserted structurally over the table's columns, not by remembering which ones to check.
- **At most one satsang is live.** A partial unique index, not Python, so two requests racing to go
  live cannot both win.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.satsang import SATSANG_STATUSES, SatsangSession
from contracts.chat.satsang import SatsangStatus
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import User as DbUser

LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)


def _person(db_session, name="Swami Ananda", role="elder"):
    user = DbUser(name=name, preferred_language="te", role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _key_hash() -> str:
    """What the gateway stores for a freshly generated key: the hash, never the key."""
    return hashlib.sha256(secrets.token_urlsafe(32).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _row(db_session, speaker=None, creator=None, **overrides) -> SatsangSession:
    speaker = speaker or _person(db_session)
    creator = creator or _person(db_session, "Moderator", "moderator")
    fields = {
        "title": "Thursday discourse",
        "speaker_user_id": speaker.id,
        "scheduled_start_at": _now() + timedelta(hours=2),
        "stream_key_sha256": _key_hash(),
        "created_by": creator.id,
    }
    fields.update(overrides)
    return SatsangSession(**fields)


def _violated(db_session, row) -> str:
    """Flush a row that must be refused; return the NAME of the constraint or index that refused
    it. Asserting the name, not just 'some IntegrityError', is what stops a test passing because of
    an unrelated NOT NULL."""
    db_session.add(row)
    with pytest.raises(IntegrityError) as caught:
        db_session.flush()
    name = caught.value.orig.diag.constraint_name
    db_session.rollback()
    return name


def _live(db_session, **overrides) -> SatsangSession:
    return _row(db_session, status="live", started_at=_now(), **overrides)


# --- defaults and the happy path ------------------------------------------------------------------


def test_a_new_satsang_is_scheduled_and_has_not_started(db_session) -> None:
    row = _row(db_session)
    db_session.add(row)
    db_session.commit()
    db_session.expire_all()
    stored = db_session.get(SatsangSession, row.id)
    assert stored.status == "scheduled"
    assert stored.started_at is None and stored.ended_at is None
    assert stored.reminder_sent_at is None
    assert stored.created_at is not None


def test_the_status_words_are_exactly_the_contracts() -> None:
    """Drift guard: the CHECK and the contract's enum must be the same four words."""
    assert set(SATSANG_STATUSES) == {s.value for s in SatsangStatus}


# --- the stream key: the database never holds it ---------------------------------------------------


def test_the_table_has_no_column_that_could_hold_a_stream_key() -> None:
    columns = set(SatsangSession.__table__.columns.keys())
    assert "stream_key_sha256" in columns
    keyish = {c for c in columns if "key" in c and c != "stream_key_sha256"}
    assert keyish == set(), f"a column that could hold a plaintext key: {keyish}"


@pytest.mark.parametrize(
    "not_a_hash",
    ["", "short", "x" * 64, secrets.token_urlsafe(32), "A" * 64, "0" * 63, "0" * 65],
)
def test_only_a_sha256_hex_digest_may_be_stored_in_the_hash_column(db_session, not_a_hash) -> None:
    """A plaintext key pasted into the wrong column is refused outright rather than stored."""
    name = _violated(db_session, _row(db_session, stream_key_sha256=not_a_hash))
    assert name == "ck_sessions_key_hash_shape"


def test_two_satsangs_cannot_share_a_key(db_session) -> None:
    shared = _key_hash()
    db_session.add(_row(db_session, stream_key_sha256=shared))
    db_session.flush()
    refused_by = _violated(db_session, _row(db_session, stream_key_sha256=shared))
    assert refused_by == "uq_sessions_stream_key_sha256"


# --- at most one live ------------------------------------------------------------------------------


def test_a_second_live_satsang_is_refused_by_the_database(db_session) -> None:
    db_session.add(_live(db_session))
    db_session.flush()
    assert _violated(db_session, _live(db_session)) == "uq_sessions_one_live"


def test_the_one_live_rule_does_not_restrict_anything_else(db_session) -> None:
    """Many scheduled, cancelled and ended beside one live, or the index is drawn too wide."""
    db_session.add_all([_row(db_session) for _ in range(3)])
    db_session.add_all([_row(db_session, status="cancelled") for _ in range(2)])
    for _ in range(3):
        db_session.add(_row(db_session, status="ended", started_at=LONG_AGO, ended_at=LATER))
    db_session.add(_live(db_session))
    db_session.commit()
    assert db_session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 9


def test_once_the_live_one_ends_another_may_go_live(db_session) -> None:
    first = _live(db_session)
    db_session.add(first)
    db_session.commit()
    first.status = "ended"
    first.ended_at = _now()
    db_session.commit()
    db_session.add(_live(db_session))
    db_session.commit()  # no IntegrityError


# --- the timestamps agree with the status ----------------------------------------------------------


@pytest.mark.parametrize(
    "overrides, constraint",
    [
        ({"status": "live"}, "ck_sessions_started_at_iff_started"),
        (
            {"status": "ended", "ended_at": LATER},
            "ck_sessions_started_at_iff_started",
        ),
        (
            {"status": "scheduled", "started_at": LONG_AGO},
            "ck_sessions_started_at_iff_started",
        ),
        (
            {"status": "cancelled", "started_at": LONG_AGO},
            "ck_sessions_started_at_iff_started",
        ),
        (
            {"status": "ended", "started_at": LONG_AGO},
            "ck_sessions_ended_at_iff_ended",
        ),
        (
            {"status": "live", "started_at": LONG_AGO, "ended_at": LATER},
            "ck_sessions_ended_at_iff_ended",
        ),
        (
            {"status": "ended", "started_at": LATER, "ended_at": LONG_AGO},
            "ck_sessions_end_after_start",
        ),
    ],
)
def test_a_status_and_its_timestamps_must_agree(db_session, overrides, constraint) -> None:
    assert _violated(db_session, _row(db_session, **overrides)) == constraint


def test_a_satsang_cancelled_after_its_reminder_went_out_is_ordinary(db_session) -> None:
    """reminder_sent_at is tied to no status: a reminder and then a cancellation happens."""
    db_session.add(_row(db_session, status="cancelled", reminder_sent_at=_now()))
    db_session.commit()  # no IntegrityError


# --- the rest of the shape --------------------------------------------------------------------------


def test_a_status_outside_the_four_is_refused(db_session) -> None:
    assert _violated(db_session, _row(db_session, status="paused")) == "ck_sessions_status"


@pytest.mark.parametrize("title", ["", "x" * 121])
def test_a_title_must_be_between_one_and_120_characters(db_session, title) -> None:
    assert _violated(db_session, _row(db_session, title=title)) == "ck_sessions_title_length"


def test_a_person_with_a_satsang_cannot_be_deleted_out_from_under_it(db_session) -> None:
    """RESTRICT, as `rooms.created_by`: the schedule is a record, not the speaker's own data."""
    speaker = _person(db_session)
    db_session.add(_row(db_session, speaker=speaker))
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": speaker.id})
    db_session.rollback()


def test_a_satsang_must_name_a_real_speaker_and_a_real_creator(db_session) -> None:
    ghost = uuid.uuid4()
    assert "speaker_user_id" in _violated(db_session, _row(db_session, speaker_user_id=ghost))
    assert "created_by" in _violated(db_session, _row(db_session, created_by=ghost))


# --- the migration made what the model declares ----------------------------------------------------


def test_the_migration_created_every_constraint_and_index_the_model_declares(db_session) -> None:
    """The live database is the migration's work; the model is what the tests above exercise. If
    the two ever name different things, a rule here would be passing against a table that does not
    really have it."""
    table = SatsangSession.__table__
    declared = {c.name for c in table.constraints if c.name} | {i.name for i in table.indexes}
    in_db = {
        row[0]
        for row in db_session.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'sessions'::regclass "
                "UNION SELECT indexname FROM pg_indexes WHERE tablename = 'sessions'"
            )
        )
    }
    missing = declared - in_db
    assert not missing, f"declared by the model but absent from the database: {missing}"
    expected = {
        "ck_sessions_status",
        "ck_sessions_title_length",
        "ck_sessions_started_at_iff_started",
        "ck_sessions_ended_at_iff_ended",
        "ck_sessions_end_after_start",
        "ck_sessions_key_hash_shape",
        "uq_sessions_one_live",
        "uq_sessions_stream_key_sha256",
    }
    assert expected <= in_db, f"expected but absent from the database: {expected - in_db}"
