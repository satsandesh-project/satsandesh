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
import uuid
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from contracts.ai.common import AudioFormat as AiAudioFormat
from contracts.ai.common import AudioRef
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.repository import claim_next_job, complete_job, fail_job, get_media_object

logger = logging.getLogger(__name__)

# A handler gets its own work session (see _run_one_claimed_job) and the
# job's payload -- every handler below is a plain function, no FastAPI, no
# HTTP framing, same "no framework in this layer" rule app/db/repository.py
# already follows.
JobHandler = Callable[[Session, dict], None]

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
        raise ValueError(
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
                handler(work_session, payload)
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
