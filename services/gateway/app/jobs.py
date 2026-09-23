"""Week 6: the background worker that drains app/db/models.py's `jobs`
table -- see that model's own docstring for why this is a table a worker
polls, not app/undo.py's in-memory `dict[str, asyncio.Task]` (lost on
restart, invisible to a second process).

Runs inside the gateway's own process, started from app/main.py's
lifespan -- not a separate service. That's deliberate: this week's
deliverable is proving that killing and restarting the gateway process
loses no queued or in-flight work, which only means something if the
worker being proved against is the same process being killed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.repository import claim_next_job, complete_job, fail_job

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict], None]

# Every job_type this worker knows how to run. "noop" and "sleep" exist to
# prove the queue's own mechanics -- claim, complete, retry with backoff,
# dead-letter past max_attempts, lease-based reclaim -- durably survive a
# real process kill (see Step 3's report for the actual kill-and-restart
# transcript; "sleep" specifically is what makes that transcript
# reproducible rather than a timing coincidence -- it gives a real
# `docker compose kill` a predictable window to land while a job is
# genuinely still claimed and running). Nothing in this repo enqueues
# real work through this queue yet; wiring an actual feature onto it is
# separate, later work, same as app/db/models.py's MediaObject noting it
# isn't wired to messages yet.
_HANDLERS: dict[str, JobHandler] = {
    "noop": lambda payload: None,
    "sleep": lambda payload: time.sleep(payload.get("seconds", 1)),
}


def worker_id() -> str:
    # Not a random uuid: a worker's own OS pid, stable for that process's
    # whole life and visible in `docker compose ps`/logs, so a stuck lease's
    # claimed_by column tells you directly which process (or which restart
    # of it) to go look at.
    return f"gateway-{os.getpid()}"


def _run_one_claimed_job(*, this_worker_id: str) -> bool:
    """Claim and run at most one job. Returns True if a job was claimed
    (whether the handler then succeeded or failed), False if nothing was
    eligible -- the caller uses this to decide whether to poll again
    immediately or sleep out the poll interval."""
    claim_session = SessionLocal()
    try:
        job = claim_next_job(
            claim_session,
            worker_id=this_worker_id,
            lease_seconds=get_settings().JOB_LEASE_SECONDS,
        )
        claim_session.commit()
        if job is None:
            return False
        job_id, job_type, payload = job.id, job.job_type, job.payload
    finally:
        claim_session.close()

    # The handler runs against its own session, deliberately separate
    # from the one that just claimed the job -- so if a handler does real
    # DB work and then raises, rolling back undoes only ITS OWN
    # transaction, never touching the claim above, which already
    # committed. See app/db/models.py's Job docstring and
    # tests/test_job_queue.py's rollback test for why that separation is
    # the point, not an accident.
    work_session = SessionLocal()
    try:
        handler = _HANDLERS.get(job_type)
        if handler is None:
            fail_job(work_session, job_id, error=f"no handler registered for job_type={job_type!r}")
        else:
            try:
                handler(payload)
            except Exception as exc:  # noqa: BLE001 -- any handler failure is a retryable job failure, not a worker crash
                work_session.rollback()
                fail_job(work_session, job_id, error=str(exc))
            else:
                complete_job(work_session, job_id)
        work_session.commit()
    finally:
        work_session.close()
    return True


async def run_worker_loop(stop_event: asyncio.Event, *, this_worker_id: str) -> None:
    """Polls for claimable jobs until `stop_event` is set. Each poll's DB
    work is blocking (SessionLocal is the same sync sessionmaker every
    other repository function in this codebase uses) -- run through
    asyncio.to_thread so a slow claim or handler never stalls the event
    loop the WS handler and every other route share."""
    poll_interval = get_settings().JOB_POLL_INTERVAL_SECONDS
    while not stop_event.is_set():
        try:
            claimed = await asyncio.to_thread(_run_one_claimed_job, this_worker_id=this_worker_id)
        except Exception:
            logger.exception("job worker: unexpected error, retrying after poll interval")
            claimed = False
        if not claimed:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass
