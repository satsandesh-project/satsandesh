"""`enqueue_job(..., next_attempt_at=...)`: a job that becomes due LATER.

`jobs.next_attempt_at` is a due time (`claim_next_job` claims a queued job only when it is <= now, and
orders by it), but `enqueue_job` had no way to set it: it relied on the column's server default, now().
This is the one additive keyword argument a scheduled job (the satsang reminder) needs. It is a shared
file, so these tests also pin that every existing caller is unaffected: the default path is the
column's server default, and tests/test_job_queue.py passes untouched.

The clock is injected, never slept: `claim_next_job` reads `datetime.now(UTC)` from
app.db.repository, so a test swaps that module's `datetime` for a subclass whose now() it controls.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db import repository
from app.db.repository import claim_next_job, enqueue_job

T0 = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class _Clock:
    """A stand-in for app.db.repository.datetime whose now() is whatever the test says."""

    def __init__(self, monkeypatch, start: datetime) -> None:
        self.current = start
        outer = self

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return outer.current if tz is None else outer.current.astimezone(tz)

        monkeypatch.setattr(repository, "datetime", FrozenDatetime)

    def advance_to(self, when: datetime) -> None:
        self.current = when


@pytest.fixture
def clock(monkeypatch):
    return _Clock(monkeypatch, T0)


def test_a_job_with_no_due_time_is_due_now_exactly_as_before(db_session):
    """The default path: nothing passed, so the column's server default (now()) decides."""
    before = datetime.now(UTC) - timedelta(seconds=5)
    job = enqueue_job(db_session, job_type="noop", payload={})
    db_session.refresh(job)
    assert before <= job.next_attempt_at <= datetime.now(UTC) + timedelta(seconds=5)
    assert claim_next_job(db_session, worker_id="w") is not None


def test_passing_none_is_the_same_as_passing_nothing(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={}, next_attempt_at=None)
    db_session.refresh(job)
    assert abs((datetime.now(UTC) - job.next_attempt_at).total_seconds()) < 5
    assert claim_next_job(db_session, worker_id="w") is not None


def test_a_job_due_later_is_stored_with_that_due_time(db_session):
    due = T0 + timedelta(hours=2)
    job = enqueue_job(db_session, job_type="noop", payload={}, next_attempt_at=due)
    db_session.refresh(job)
    assert job.next_attempt_at == due
    assert job.status == "queued"


def test_a_job_due_later_is_not_claimed_before_its_due_time(db_session, clock):
    due = T0 + timedelta(hours=2)
    enqueue_job(db_session, job_type="noop", payload={}, next_attempt_at=due)
    for offset in (timedelta(0), timedelta(hours=1), timedelta(hours=2) - timedelta(seconds=1)):
        clock.advance_to(T0 + offset)
        assert claim_next_job(db_session, worker_id="w") is None, f"claimed {offset} early"


def test_a_job_due_later_is_claimed_once_it_is_due(db_session, clock):
    due = T0 + timedelta(hours=2)
    job = enqueue_job(db_session, job_type="noop", payload={}, next_attempt_at=due)
    clock.advance_to(due)
    claimed = claim_next_job(db_session, worker_id="w")
    assert claimed is not None and claimed.id == job.id
    assert (claimed.status, claimed.attempts) == ("running", 1)


def test_a_due_time_already_past_is_claimable_at_once(db_session, clock):
    job = enqueue_job(
        db_session, job_type="noop", payload={}, next_attempt_at=T0 - timedelta(minutes=10)
    )
    claimed = claim_next_job(db_session, worker_id="w")
    assert claimed is not None and claimed.id == job.id


def test_the_earlier_due_job_is_claimed_first(db_session, clock):
    later = enqueue_job(
        db_session, job_type="a", payload={}, next_attempt_at=T0 + timedelta(hours=3)
    )
    sooner = enqueue_job(
        db_session, job_type="b", payload={}, next_attempt_at=T0 + timedelta(hours=1)
    )
    clock.advance_to(T0 + timedelta(hours=4))
    assert claim_next_job(db_session, worker_id="w").id == sooner.id
    assert claim_next_job(db_session, worker_id="w").id == later.id


def test_a_not_yet_due_job_does_not_hold_back_one_that_is_due(db_session, clock):
    enqueue_job(db_session, job_type="later", payload={}, next_attempt_at=T0 + timedelta(hours=5))
    ready = enqueue_job(db_session, job_type="ready", payload={})
    claimed = claim_next_job(db_session, worker_id="w")
    assert claimed is not None and claimed.id == ready.id


def test_a_due_time_without_a_timezone_is_refused(db_session):
    """A naive datetime would be read in the database session's zone: refuse it, don't guess."""
    with pytest.raises(ValueError, match="timezone"):
        enqueue_job(
            db_session, job_type="noop", payload={}, next_attempt_at=datetime(2026, 10, 20, 12, 0)
        )


def test_the_other_arguments_still_work_alongside_it(db_session):
    job = enqueue_job(
        db_session,
        job_type="noop",
        payload={"k": 1},
        max_attempts=2,
        next_attempt_at=T0 + timedelta(minutes=1),
    )
    assert (job.job_type, job.payload, job.max_attempts) == ("noop", {"k": 1}, 2)


def test_the_argument_is_keyword_only_so_no_positional_caller_can_shift(db_session):
    with pytest.raises(TypeError):
        enqueue_job(db_session, "noop", {}, 5, T0)  # type: ignore[misc]
