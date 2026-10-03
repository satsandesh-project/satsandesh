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
import secrets
import threading
import time
import uuid
from collections.abc import Callable

import httpx
from contracts.ai.common import AudioFormat as AiAudioFormat
from contracts.ai.common import AudioRef
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.repository import (
    claim_next_job,
    complete_job,
    extend_job_lease,
    fail_job,
    get_media_object,
)

logger = logging.getLogger(__name__)

# A handler gets its own work session (see _run_one_claimed_job) and the
# job's payload -- every handler below is a plain function, no FastAPI, no
# HTTP framing, same "no framework in this layer" rule app/db/repository.py
# already follows.
JobHandler = Callable[[Session, dict], None]


class PermanentJobError(Exception):
    """Raised by a handler for a failure no retry can fix. The worker lands
    the job in 'dead' immediately (with this message as last_error) instead
    of backing off and retrying max_attempts times first. Anything else a
    handler raises is treated as transient and retried."""


# contracts/chat/'s media format enum and contracts/ai/'s audio format
# enum don't fully agree -- webm_opus (a real, allowed
# app/db/models.py::MediaObject.format value) has no counterpart in
# contracts/ai/common.py's AudioFormat. Not something this file can fix
# (contracts/ai/ is out of scope this week) -- see OPEN_QUESTIONS.md.
# _handle_transcribe_media fails loudly (fail_job) rather than silently
# mislabeling a webm_opus upload as some other format.
_AI_AUDIO_FORMAT_BY_CHAT_FORMAT: dict[str, AiAudioFormat] = {
    "wav_pcm16": AiAudioFormat.WAV_PCM16,
    "ogg_opus": AiAudioFormat.OGG_OPUS,
    "mp3": AiAudioFormat.MP3,
}


def _handle_transcribe_media(session: Session, payload: dict) -> None:
    """Week 6 Step 2: proves a real job can flow through this queue and
    reach services/ai/mock/ for anything AI-side -- never real ASR, which
    doesn't exist to call yet (Week 8). Deliberately does not persist the
    transcript anywhere: there's no column or table for one yet, and
    inventing that storage is Week 7's orchestrator design
    ("denoise -> transcribe -> pivot -> moderate -> render",
    docs/retro/month-1.md), not this week's. The mock's response is
    logged instead -- enough to prove the call really happened and really
    reached the mock, not real ASR."""
    media_id = uuid.UUID(payload["media_id"])
    media = get_media_object(session, media_id)
    if media is None:
        # Gone before this job got to run -- swept by retention, most
        # likely (app/retention.py runs on its own timer, independently
        # of this queue). Nothing left to transcribe; not a failure of
        # this job's own work.
        return

    audio_format = _AI_AUDIO_FORMAT_BY_CHAT_FORMAT.get(media.format)
    if audio_format is None:
        raise PermanentJobError(
            f"no contracts.ai AudioFormat for chat format {media.format!r} "
            "(see app/jobs.py's _AI_AUDIO_FORMAT_BY_CHAT_FORMAT)"
        )

    request = TranscribeRequest(
        audio=AudioRef(uri=f"media:{media.id}", format=audio_format, duration_ms=media.duration_ms)
    )
    ai_service_url = get_settings().AI_SERVICE_URL
    response = httpx.post(
        f"{ai_service_url}/v1/transcribe",
        json=request.model_dump(mode="json"),
        timeout=10.0,
    )
    # A 4xx (other than "slow down"/"timed out") means the AI service
    # rejected THIS request -- retrying the identical request can't change
    # that. A 5xx or an unreachable service is transient and is retried.
    if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
        raise PermanentJobError(
            f"AI service rejected the transcribe request with HTTP {response.status_code}"
        )
    response.raise_for_status()
    result = TranscribeResponse.model_validate(response.json())
    logger.info(
        "transcribe_media: media_id=%s model_version=%s detected_language=%s text=%r",
        media_id,
        result.model_version,
        result.detected_language,
        result.text,
    )


# Every job_type this worker knows how to run. "noop" and "sleep" exist to
# prove the queue's own mechanics -- claim, complete, retry with backoff,
# dead-letter past max_attempts, lease-based reclaim -- durably survive a
# real process kill (see Step 3's report for the actual kill-and-restart
# transcript; "sleep" specifically is what makes that transcript
# reproducible rather than a timing coincidence -- it gives a real
# `docker compose kill` a predictable window to land while a job is
# genuinely still claimed and running). "transcribe_media" is this
# queue's first real consumer, enqueued by app/media.py's upload_media.
_HANDLERS: dict[str, JobHandler] = {
    "noop": lambda session, payload: None,
    "sleep": lambda session, payload: time.sleep(payload.get("seconds", 1)),
    "transcribe_media": _handle_transcribe_media,
}


def register_handler(job_type: str, handler: JobHandler) -> None:
    """Register a handler for a job_type without editing _HANDLERS directly.

    _HANDLERS stays module-private (no __all__ export) so its internal shape
    can change later -- e.g. per-handler config, not just a bare callable --
    without that being a breaking change for callers. A caller outside this
    module (e.g. a future moderate_message handler owned by someone else)
    calls this at startup, the same place transcribe_media gets wired in
    app/main.py's lifespan, instead of importing and mutating _HANDLERS.
    Raises if job_type is already registered, since a silent overwrite here
    would mean the second registration's handler quietly wins and the first
    stops running with no error anywhere.
    """
    if job_type in _HANDLERS:
        raise ValueError(f"handler already registered for job_type={job_type!r}")
    _HANDLERS[job_type] = handler


# Fixed for this process's whole life, but NOT just the OS pid: a container's
# pid is 1 on every fresh start, so a restarted worker would be
# indistinguishable from the one it replaced in jobs.claimed_by -- and the
# ownership checks (complete_job/fail_job/extend_job_lease worker_id=)
# can't tell the old holder from the new one if the ids collide. The random
# suffix makes each process incarnation unique; the pid stays for humans
# reading logs or `docker compose ps`.
_WORKER_ID = f"gateway-{os.getpid()}-{secrets.token_hex(4)}"


def worker_id() -> str:
    return _WORKER_ID


def _heartbeat(job_id: uuid.UUID, this_worker_id: str, lease_seconds: int, stop: threading.Event):
    """Renew this job's lease every lease/3 seconds while its handler runs,
    so a job longer than JOB_LEASE_SECONDS is never handed to a second
    worker mid-flight. Stops when the handler finishes (`stop`), or as soon
    as the job is no longer ours (extend_job_lease returns False)."""
    interval = max(0.5, lease_seconds / 3)
    while not stop.wait(interval):
        session = SessionLocal()
        try:
            still_ours = extend_job_lease(
                session, job_id, worker_id=this_worker_id, lease_seconds=lease_seconds
            )
            session.commit()
        except Exception:
            logger.exception("job %s: lease heartbeat failed, will retry", job_id)
            continue
        finally:
            session.close()
        if not still_ours:
            logger.warning(
                "job %s: no longer held by %s, stopping heartbeat", job_id, this_worker_id
            )
            return


def _run_one_claimed_job(*, this_worker_id: str) -> bool:
    """Claim and run at most one job. Returns True if a job was claimed
    (whether the handler then succeeded or failed), False if nothing was
    eligible -- the caller uses this to decide whether to poll again
    immediately or sleep out the poll interval."""
    lease_seconds = get_settings().JOB_LEASE_SECONDS
    claim_session = SessionLocal()
    try:
        job = claim_next_job(
            claim_session,
            worker_id=this_worker_id,
            lease_seconds=lease_seconds,
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
    stop_heartbeat = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(job_id, this_worker_id, lease_seconds, stop_heartbeat),
        name=f"job-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat.start()
    work_session = SessionLocal()
    try:
        handler = _HANDLERS.get(job_type)
        if handler is None:
            outcome = fail_job(
                work_session,
                job_id,
                error=f"no handler registered for job_type={job_type!r}",
                worker_id=this_worker_id,
            )
        else:
            try:
                handler(work_session, payload)
            except PermanentJobError as exc:
                work_session.rollback()
                outcome = fail_job(
                    work_session, job_id, error=str(exc), worker_id=this_worker_id, permanent=True
                )
            except Exception as exc:  # noqa: BLE001 -- any other handler failure is a retryable job failure, not a worker crash
                work_session.rollback()
                outcome = fail_job(work_session, job_id, error=str(exc), worker_id=this_worker_id)
            else:
                outcome = (
                    "done"
                    if complete_job(work_session, job_id, worker_id=this_worker_id)
                    else "lost"
                )
        work_session.commit()
        if outcome == "lost":
            # The lease was lost mid-run (heartbeat failing for a whole
            # lease, or the process was suspended) and another worker owns
            # this job now: our result is discarded, theirs stands.
            logger.warning("job %s: finished but no longer held by %s", job_id, this_worker_id)
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=5)
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
