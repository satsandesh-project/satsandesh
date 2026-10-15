"""The satsang reminder: a push, thirty minutes before a satsang starts (Week 10).

It rides the existing job queue, so nothing here is a clock or a sweep loop. Scheduling a satsang
enqueues ONE `satsang_reminder` job with `jobs.next_attempt_at = scheduled_start_at - 30 min`;
`claim_next_job` (app/db/repository.py) claims a queued job only when that time has passed, and the
queue already supplies retries with backoff, dead-lettering after JOB_MAX_ATTEMPTS, and reclaiming a
job whose worker died. This module is the handler and the one place a job is made.

**A satsang that is moved after its reminder was enqueued (design (b)).** The handler does not trust
what was true when the job was made: it re-reads the satsang's CURRENT `scheduled_start_at`. If the
start was moved later and the job fires too early, it enqueues itself again for the new time and
sends nothing; if it fires at the right time, it sends. I chose this over (a), re-enqueue on edit
and cancel the old job, because the satsang row is then the single source of truth: there is no old
job to find, nothing to cancel, and so no orphan that could still fire at the old time (which would
be worse than no job). The costs, stated: one wake-up when a satsang is moved later; and a satsang
moved EARLIER than its old reminder time is reminded at the OLD time (late), or not at all if it
has started by then (`test_KNOWN_LIMIT_...`). There is no reschedule route yet; the one that adds it
should also enqueue a fresh job with `enqueue_reminder`, which is safe because the handler is
idempotent.

**Sent once.** `reminder_sent_at` is a timestamp, not a flag, and it is set in the same session as
the job's completion: the worker commits the handler's writes and `complete_job` in ONE transaction
(app/jobs.py), so a job cannot be done without the timestamp, nor the timestamp without the job. A
second run (a duplicate job, a rerun) sees it and sends nothing. What cannot be made atomic is the
push itself, a network call: a process that dies after the pushes and before that commit will send
again on the retry. After the commit it never does. A duplicate "starting in 30 minutes" is the
cheaper mistake than a missing one, so the push comes first and the timestamp second.

**Who, and what is NOT here.** Every user with a push subscription, once, however many devices,
unless it is inside their own quiet hours (the rule every other push follows, app/push.py; a user
with no timezone is never silenced). That a reminder is held back for an evening satsang that falls
in the default quiet window is a consequence worth the owner's attention. The text is English only
for now: the reminder in each user's language, and the spoken (TTS) version the plan describes, are
not built.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import PushSubscription
from app.db.models import User as DbUser
from app.db.repository import enqueue_job
from app.db.satsang import SatsangSession
from app.jobs import PermanentJobError
from app.push import is_quiet_hours, send_push

logger = logging.getLogger(__name__)

JOB_TYPE = "satsang_reminder"
LEAD = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(UTC)


def reminder_due_at(scheduled_start_at: datetime) -> datetime:
    return scheduled_start_at - LEAD


def enqueue_reminder(db: Session, row: SatsangSession):
    """The one place a reminder job is made. Called inside the transaction that saves the satsang,
    so a satsang that is not saved has no reminder and a saved one cannot lack it."""
    return enqueue_job(
        db,
        job_type=JOB_TYPE,
        payload={"session_id": str(row.id)},
        max_attempts=get_settings().JOB_MAX_ATTEMPTS,
        next_attempt_at=reminder_due_at(row.scheduled_start_at),
    )


def deliver(session: Session, row: SatsangSession, minutes_left: int) -> None:
    """Push every subscribed user who is not in their quiet hours. A seam: the tests replace it."""
    now = _now()
    unit = "minute" if minutes_left == 1 else "minutes"
    title = "Satsang soon"
    body = f"{row.title} starts in {minutes_left} {unit}"
    user_ids = sorted(session.scalars(select(PushSubscription.user_id).distinct()), key=str)
    for user_id in user_ids:
        user = session.get(DbUser, user_id)
        if user is None:
            continue
        if is_quiet_hours(
            timezone=user.timezone,
            quiet_hours_start=user.quiet_hours_start,
            quiet_hours_end=user.quiet_hours_end,
            now=now,
        ):
            continue
        send_push(session, user_id=user_id, title=title, body=body)


def handle_satsang_reminder(session: Session, payload: dict) -> None:
    """Run by the job worker, which commits whatever this writes together with the job's completion
    and rolls it all back (and retries) if this raises. Every early return below is a COMPLETED job:
    there is nothing to retry."""
    try:
        session_id = uuid.UUID(str(payload["session_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"a satsang_reminder job needs a session_id: {payload!r}") from exc

    row = session.execute(
        select(SatsangSession).where(SatsangSession.id == session_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        logger.info("satsang reminder %s: no such satsang, nothing to remind", session_id)
        return
    if row.status != "scheduled":
        logger.info("satsang reminder %s: it is %s, not reminding", row.id, row.status)
        return
    if row.reminder_sent_at is not None:
        logger.info("satsang reminder %s: already sent", row.id)
        return

    now = _now()
    start = row.scheduled_start_at
    if start <= now:
        logger.info("satsang reminder %s: its start has passed, not reminding", row.id)
        return
    if now < reminder_due_at(start):
        # Moved later since this job was made: ask again for the new time, send nothing now.
        enqueue_reminder(session, row)
        logger.info("satsang reminder %s: moved later, re-enqueued for its new time", row.id)
        return

    deliver(session, row, max(1, math.ceil((start - now).total_seconds() / 60)))
    row.reminder_sent_at = now
    session.flush()
    logger.info("satsang reminder %s: sent", row.id)
