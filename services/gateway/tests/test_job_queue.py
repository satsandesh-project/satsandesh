"""Tests for the durable job queue (app/db/models.py::Job,
app/db/repository.py's job functions) — a table with rows a worker polls,
specifically NOT services/gateway/app/undo.py's in-memory
dict[str, asyncio.Task] pattern (see that file's own docstring for why:
a task scheduled on process A is invisible to process B, and everything
pending is lost on restart). This week's deliverable is "notes survive a
saturated CPU" -- these tests are what that claim has to survive against
before Step 3's real kill-and-restart proof.

Written before app/db/repository.py's job functions exist — collecting
this file is expected to fail (ImportError) until Step 3's implementation
lands. That failure is the point: these tests fix the contract the
implementation has to satisfy, not the other way around.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import Job
from app.db.repository import (
    claim_next_job,
    compute_backoff_seconds,
    complete_job,
    enqueue_job,
    fail_job,
)


def test_enqueue_job_persists_and_returns_row(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={"media_id": "abc"})

    assert job.id is not None
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.job_type == "media.process"
    assert job.payload == {"media_id": "abc"}


def test_queued_job_gets_claimed(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={})

    claimed = claim_next_job(db_session, worker_id="worker-1")

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.claimed_by == "worker-1"
    assert claimed.attempts == 1


def test_claim_returns_none_when_nothing_queued(db_session):
    assert claim_next_job(db_session, worker_id="worker-1") is None


def test_claim_then_complete_marks_job_done(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={})
    claim_next_job(db_session, worker_id="worker-1")

    complete_job(db_session, job.id)
    db_session.commit()
    db_session.expire_all()

    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == "done"


def test_a_job_that_raises_is_retried_and_attempts_increments(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={}, max_attempts=5)
    claimed = claim_next_job(db_session, worker_id="worker-1")
    assert claimed.attempts == 1

    # Simulate the handler raising -- fail_job is what a worker calls from
    # its except block, never a second claim.
    status = fail_job(db_session, job.id, error="synthetic failure for this test")
    db_session.commit()
    db_session.expire_all()

    refreshed = db_session.get(Job, job.id)
    assert status == "queued"
    assert refreshed.status == "queued"
    assert refreshed.attempts == 1  # unchanged by fail_job -- claim already counted it
    assert refreshed.last_error == "synthetic failure for this test"

    # And the retry is real: claiming again increments attempts again.
    reclaimed = claim_next_job(db_session, worker_id="worker-1", ignore_backoff=True)
    assert reclaimed is not None
    assert reclaimed.attempts == 2


def test_rollback_test_attempts_survives_a_failed_jobs_transaction(db_session, engine):
    # services/auth/DECISIONS.md D11 (services/auth/ doesn't exist in this
    # repo, checked -- but the bug class it records is real and has hit
    # this team's own code before, per app/db/repository.py's own
    # SAVEPOINT-recovery comments elsewhere): a counter incremented inside
    # a transaction that then fails is rolled back with it, so it protects
    # nothing while reading as though it works.
    #
    # This is why claim_next_job's attempts increment must be committed on
    # its own, before any job handler runs -- not bundled into whatever
    # transaction the handler's own work happens to be using. Proven here
    # directly: claim a job (its own committed transaction, via a SEPARATE
    # session so this test can roll back the other session without
    # touching the claim), then open a second session, do some work AND
    # attempt to read/act on attempts, and roll THAT session back without
    # committing -- the attempts value from the claim must still be there
    # afterward, read fresh from the database, not from this session's own
    # possibly-stale view.
    claim_session = Session(engine)
    job = enqueue_job(claim_session, job_type="media.process", payload={})
    claim_session.commit()
    claimed = claim_next_job(claim_session, worker_id="worker-1")
    claim_session.commit()
    assert claimed.attempts == 1
    claim_session.close()

    # A second, independent session does some work in a transaction that
    # never commits -- simulating a handler that raised partway through
    # its own DB work (e.g. a partially-written side effect) -- then rolls
    # back instead of committing, exactly what a real exception handler
    # unwinding through `with session.begin():` or an unhandled exception
    # does.
    doomed_session = Session(engine)
    try:
        doomed_session.execute(
            Job.__table__.update()
            .where(Job.id == job.id)
            .values(last_error="this write must NOT survive")
        )
        # Never committed -- roll back, simulating the handler's failure
        # unwinding without ever reaching a commit.
        doomed_session.rollback()
    finally:
        doomed_session.close()

    # A third, fresh session reads the row from scratch -- proves the
    # claim's attempts increment (committed earlier, in its own
    # transaction) survived, and the doomed session's uncommitted write
    # did NOT leak through.
    verify_session = Session(engine)
    try:
        refreshed = verify_session.get(Job, job.id)
        assert refreshed.attempts == 1, (
            "attempts did not survive the failed transaction -- exactly the "
            "bug class this test exists to catch: if the increment happened "
            "in the same transaction as the failing work instead of its own "
            "committed step, retries would never actually be counted, and a "
            "poisoned job would retry forever."
        )
        assert refreshed.last_error != "this write must NOT survive"
    finally:
        verify_session.close()


def test_job_exceeding_max_attempts_lands_in_dead_state(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={}, max_attempts=2)

    claim_next_job(db_session, worker_id="worker-1")
    status_1 = fail_job(db_session, job.id, error="first failure")
    db_session.commit()
    assert status_1 == "queued"

    claim_next_job(db_session, worker_id="worker-1", ignore_backoff=True)
    status_2 = fail_job(db_session, job.id, error="second failure -- exhausted")
    db_session.commit()
    db_session.expire_all()

    refreshed = db_session.get(Job, job.id)
    assert status_2 == "dead"
    assert refreshed.status == "dead"
    assert refreshed.attempts == 2
    assert refreshed.last_error == "second failure -- exhausted"


def test_dead_job_is_not_picked_up_again(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={}, max_attempts=1)
    claim_next_job(db_session, worker_id="worker-1")
    fail_job(db_session, job.id, error="only attempt, exhausted immediately")
    db_session.commit()

    assert claim_next_job(db_session, worker_id="worker-2") is None


def test_two_concurrent_workers_never_claim_the_same_job(db_session, engine):
    # Same forced-interleaving technique as
    # test_create_message_with_created_flag_exactly_one_winner_under_concurrent_race
    # in tests/test_repository.py: inject a genuinely concurrent second
    # claim exactly at the moment this call has picked its candidate but
    # not yet committed the claiming UPDATE, rather than hoping two
    # sequential calls happen to race. This is the property this whole
    # design exists to guarantee -- two workers must never both believe
    # they own the same job.
    job = enqueue_job(db_session, job_type="media.process", payload={})
    db_session.commit()  # visible to the racer's independent session

    racer_session = Session(engine)
    racer_result: dict = {}
    original_execute = db_session.execute
    call_count = {"n": 0}

    def execute_with_racer_between_select_and_update(statement, *args, **kwargs):
        call_count["n"] += 1
        result = original_execute(statement, *args, **kwargs)
        if call_count["n"] == 1:
            # The first statement claim_next_job issues is the candidate
            # SELECT. Right after it returns (this session has picked a
            # candidate but not yet run its conditional UPDATE), a wholly
            # separate session claims the SAME job and commits -- the
            # actual race: by the time this session's own UPDATE reaches
            # Postgres, the racer's claim already committed out from
            # under it.
            racer_job = claim_next_job(racer_session, worker_id="racer")
            racer_session.commit()
            racer_result["claimed_id"] = racer_job.id if racer_job else None
        return result

    db_session.execute = execute_with_racer_between_select_and_update
    try:
        mine = claim_next_job(db_session, worker_id="mine")
    finally:
        db_session.execute = original_execute
        racer_session.close()

    # Exactly one winner: the racer claimed it (its own call sees the
    # job), and this call correctly gets nothing back -- never both
    # believing they hold it, never neither.
    assert racer_result["claimed_id"] == job.id
    assert mine is None


def test_a_claimed_job_whose_worker_died_is_reclaimable(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={})
    claim_next_job(db_session, worker_id="worker-that-died", lease_seconds=300)
    db_session.commit()

    # Simulate the lease having already expired -- the worker that
    # claimed this crashed or was killed and never finished, so nothing
    # ever called complete_job/fail_job to release it.
    db_session.execute(
        Job.__table__.update()
        .where(Job.id == job.id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()

    reclaimed = claim_next_job(db_session, worker_id="worker-2")

    assert reclaimed is not None
    assert reclaimed.id == job.id
    assert reclaimed.claimed_by == "worker-2"
    assert reclaimed.attempts == 2  # incremented again on the reclaim


def test_a_job_whose_lease_has_not_expired_is_not_reclaimable(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={})
    claim_next_job(db_session, worker_id="worker-1", lease_seconds=300)
    db_session.commit()

    assert claim_next_job(db_session, worker_id="worker-2") is None


def test_compute_backoff_seconds_is_exponential_and_capped():
    # Pure function, tested directly and exactly -- no DB, no timing
    # flakiness. base=5s, doubling per attempt, capped at 300s.
    assert compute_backoff_seconds(1, base=5.0, cap=300.0) == 5.0
    assert compute_backoff_seconds(2, base=5.0, cap=300.0) == 10.0
    assert compute_backoff_seconds(3, base=5.0, cap=300.0) == 20.0
    assert compute_backoff_seconds(4, base=5.0, cap=300.0) == 40.0
    assert compute_backoff_seconds(10, base=5.0, cap=300.0) == 300.0  # capped


def test_failed_job_is_not_reclaimable_until_backoff_elapses(db_session):
    job = enqueue_job(db_session, job_type="media.process", payload={}, max_attempts=5)
    claim_next_job(db_session, worker_id="worker-1")
    fail_job(db_session, job.id, error="will retry after backoff")
    db_session.commit()

    # Backoff after attempt 1 is 5s -- immediately re-polling must not
    # reclaim it yet.
    assert claim_next_job(db_session, worker_id="worker-2") is None

    # ignore_backoff=True is the test-only escape hatch used elsewhere in
    # this file to avoid sleeping in tests; here it's the thing under
    # test, so instead push next_attempt_at into the past directly.
    db_session.execute(
        Job.__table__.update()
        .where(Job.id == job.id)
        .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()

    reclaimed = claim_next_job(db_session, worker_id="worker-2")
    assert reclaimed is not None
    assert reclaimed.id == job.id
