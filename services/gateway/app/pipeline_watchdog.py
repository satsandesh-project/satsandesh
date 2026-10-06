"""OPEN_QUESTIONS #20: a message stuck behind a dead pipeline is held for a person.

The gate keeps a message from delivering while its `pipeline_state` is `pending`. That is right
while a job is working on it, and wrong forever if nothing is: the job was deleted by hand, or it
died and the dead-letter hook that would have held the message itself failed (that failure is
logged, never raised), or a handler returned without finishing. The message then sits `pending`
and nobody is told.

STUCK means: `status = 'pending'`, `pipeline_state = 'pending'`, older than a short grace period,
and **no `process_message` job that is queued or running**. A retry waiting out its backoff is
`queued`; a job whose worker died is `running` with an expired lease and is reclaimed, so both
count as alive. It is deliberately not "pending for longer than N seconds": a note can legitimately
wait minutes behind others on a CPU-only host with a live job (infra/ai/README.md, ten notes at
once), and an age rule would hold those.

A stuck message goes through the same fail-closed path as a dead-lettered job
(`pipeline._hold_for_pipeline`): it becomes `held` with a SYSTEM event, the sender's devices are told,
and it appears in the moderator queue the people already look at. No new endpoint, service or
contract. The log line and the event carry the message's id and the reason, never its text or
transcript.

Same shape as app/retention.py: a timed loop inside the gateway process, so it survives however the
service is deployed without a second thing to configure.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Text, cast, exists, select
from sqlalchemy.orm import Session

from app import pipeline
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import Job, Message

logger = logging.getLogger(__name__)

# `queued` includes a retry waiting out its backoff; `running` includes a job whose worker died
# (its lease expires and claim_next_job hands it to the next poll).
_LIVE_JOB_STATUSES = ("queued", "running")


def _has_live_job():
    """SQL: a process_message job for this message that someone is, or will be, working on."""
    return exists().where(
        Job.job_type == pipeline.JOB_TYPE,
        Job.status.in_(_LIVE_JOB_STATUSES),
        Job.payload["message_id"].astext == cast(Message.id, Text),
    )


def _message_has_live_job(session: Session, message_id: uuid.UUID) -> bool:
    return (
        session.scalar(
            select(Job.id)
            .where(
                Job.job_type == pipeline.JOB_TYPE,
                Job.status.in_(_LIVE_JOB_STATUSES),
                Job.payload["message_id"].astext == str(message_id),
            )
            .limit(1)
        )
        is not None
    )


def find_stuck_message_ids(
    session: Session, *, grace_seconds: float, now: datetime | None = None
) -> list[uuid.UUID]:
    cutoff = (now or datetime.now(UTC)) - timedelta(seconds=grace_seconds)
    return list(
        session.scalars(
            select(Message.id)
            .where(
                Message.status == "pending",
                Message.pipeline_state == "pending",
                Message.deleted_at.is_(None),
                Message.created_at < cutoff,
                ~_has_live_job(),
            )
            .order_by(Message.id)
        )
    )


def hold_stuck_messages(*, grace_seconds: float, now: datetime | None = None) -> list[str]:
    """One pass: hold every stuck message. Returns the held ids. Each message is handled in its
    own transaction, re-checked under a row lock, so a message that finished (or got a job, or was
    cancelled) between the scan and the hold is left alone."""
    with SessionLocal() as scan:
        candidates = find_stuck_message_ids(scan, grace_seconds=grace_seconds, now=now)

    held: list[str] = []
    for message_id in candidates:
        with SessionLocal() as session:
            message = session.scalars(
                select(Message).where(Message.id == message_id).with_for_update(skip_locked=True)
            ).first()
            if message is None:
                continue  # locked by someone else right now: the next pass sees it
            if (
                message.status != "pending"
                or message.pipeline_state != "pending"
                or message.deleted_at is not None
            ):
                continue
            if _message_has_live_job(session, message_id):
                continue
            pipeline._hold_for_pipeline(
                session,
                message,
                f"Pipeline stalled: no job was working on this message after {grace_seconds:g} s.",
                "failed",
            )
            held.append(str(message_id))
            logger.warning(
                "pipeline watchdog: held message %s (no queued or running job, older than %g s)",
                message_id,
                grace_seconds,
            )
    return held


async def run_pipeline_watchdog_loop(stop_event: asyncio.Event) -> None:
    """hold_stuck_messages on a timer until `stop_event` is set. asyncio.to_thread for the same
    reason as app/jobs.py and app/retention.py: SessionLocal is a sync sessionmaker and this must
    not block the event loop the WebSocket handler and every route share."""
    settings = get_settings()
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(
                hold_stuck_messages, grace_seconds=settings.PIPELINE_STUCK_GRACE_SECONDS
            )
        except Exception:
            logger.exception("pipeline watchdog: unexpected error, retrying after the interval")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=settings.PIPELINE_WATCHDOG_INTERVAL_SECONDS
            )
        except TimeoutError:
            pass
