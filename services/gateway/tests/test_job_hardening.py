"""Hardening for the job queue found after it merged (PR #71): problems the
original tests didn't exercise because they only ever ran one worker at a
time with a lease far longer than the job.

1. A worker whose lease expired could still mark a job done/failed AFTER
   another worker had legitimately reclaimed it -- complete_job/fail_job
   never checked who held the job.
2. Nothing renewed a lease: a job running longer than JOB_LEASE_SECONDS was
   handed to a second worker while the first was still honestly working.
3. worker_id() was just the OS pid, which is 1 in every fresh container, so
   claimed_by couldn't tell a restarted worker from the one it replaced.
4. A job that can NEVER succeed (a format with no AI-contract equivalent, a
   4xx from the AI service) was retried max_attempts times with backoff
   before landing dead, instead of landing dead immediately.
5. JOB_MAX_ATTEMPTS was documented and configurable but never read -- the
   default 5 was hardcoded in enqueue_job.

Written before any of this exists.
"""

import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

import app.jobs as jobs_module
from app.config import get_settings
from app.db.models import Job
from app.db.repository import (
    claim_next_job,
    complete_job,
    enqueue_job,
    extend_job_lease,
    fail_job,
)


def _expire_lease(db_session, job_id):
    db_session.execute(
        Job.__table__.update()
        .where(Job.id == job_id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()


# -- 1. ownership: a worker that lost its lease must not overwrite the new owner --------


def test_complete_job_by_a_worker_that_lost_its_lease_is_refused(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={})
    claim_next_job(db_session, worker_id="slow-worker")
    db_session.commit()
    _expire_lease(db_session, job.id)
    claim_next_job(db_session, worker_id="fast-worker")  # legitimately reclaims it
    db_session.commit()

    applied = complete_job(db_session, job.id, worker_id="slow-worker")
    db_session.commit()
    db_session.expire_all()

    assert applied is False
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == "running"
    assert refreshed.claimed_by == "fast-worker"


def test_fail_job_by_a_worker_that_lost_its_lease_is_refused(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={})
    claim_next_job(db_session, worker_id="slow-worker")
    db_session.commit()
    _expire_lease(db_session, job.id)
    claim_next_job(db_session, worker_id="fast-worker")
    db_session.commit()

    outcome = fail_job(db_session, job.id, error="late failure", worker_id="slow-worker")
    db_session.commit()
    db_session.expire_all()

    assert outcome == "lost"
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == "running"
    assert refreshed.claimed_by == "fast-worker"
    assert refreshed.last_error is None


def test_complete_job_by_the_current_owner_still_works(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={})
    claim_next_job(db_session, worker_id="w1")
    db_session.commit()

    assert complete_job(db_session, job.id, worker_id="w1") is True
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "done"


# -- 2. lease renewal ---------------------------------------------------------------


def test_extend_job_lease_pushes_the_expiry_forward_for_the_owner(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={})
    claim_next_job(db_session, worker_id="w1", lease_seconds=5)
    db_session.commit()
    db_session.expire_all()
    before = db_session.get(Job, job.id).lease_expires_at

    assert extend_job_lease(db_session, job.id, worker_id="w1", lease_seconds=300) is True
    db_session.commit()
    db_session.expire_all()

    assert db_session.get(Job, job.id).lease_expires_at > before + timedelta(seconds=200)


def test_extend_job_lease_refuses_a_worker_that_does_not_own_the_job(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={})
    claim_next_job(db_session, worker_id="w1", lease_seconds=5)
    db_session.commit()

    assert (
        extend_job_lease(db_session, job.id, worker_id="someone-else", lease_seconds=300) is False
    )


def test_extend_job_lease_refuses_a_job_that_is_not_running(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={})
    db_session.commit()

    assert extend_job_lease(db_session, job.id, worker_id="w1", lease_seconds=300) is False


def test_a_job_running_longer_than_its_lease_is_not_handed_to_a_second_worker(
    db_session, engine, monkeypatch
):
    monkeypatch.setattr(get_settings(), "JOB_LEASE_SECONDS", 2)
    job = enqueue_job(db_session, job_type="sleep", payload={"seconds": 4})
    job_id = job.id
    db_session.commit()

    worker = threading.Thread(
        target=jobs_module._run_one_claimed_job, kwargs={"this_worker_id": "worker-a"}
    )
    worker.start()
    try:
        time.sleep(3.2)  # comfortably past the 2s lease; the handler is still mid-sleep
        observer = Session(engine)
        try:
            stolen = claim_next_job(observer, worker_id="worker-b")
            observer.commit()
        finally:
            observer.close()
        assert stolen is None, (
            "a second worker reclaimed a job whose handler was still running -- "
            "the lease must be renewed while the handler works"
        )
    finally:
        worker.join(timeout=20)

    db_session.expire_all()
    final = db_session.get(Job, job_id)
    assert final.status == "done"
    assert final.attempts == 1, "the job must have run exactly once, never reclaimed"


# -- 3. worker_id -------------------------------------------------------------------


def test_worker_id_is_stable_within_a_process_and_includes_a_random_suffix():
    first = jobs_module.worker_id()
    assert first == jobs_module.worker_id()
    assert re.fullmatch(r"gateway-\d+-[0-9a-f]{8}", first), first


def test_worker_id_differs_between_processes_even_when_the_pid_would_match():
    repo_root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(repo_root), os.getcwd()])}
    code = "from app.jobs import worker_id; print(worker_id())"
    other = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    ).stdout.strip()

    assert re.fullmatch(r"gateway-\d+-[0-9a-f]{8}", other), other
    assert other != jobs_module.worker_id()


# -- 4. permanent failures go straight to dead --------------------------------------


def test_fail_job_permanent_goes_dead_immediately_regardless_of_attempts(db_session):
    job = enqueue_job(db_session, job_type="noop", payload={}, max_attempts=5)
    claim_next_job(db_session, worker_id="w1")
    db_session.commit()

    outcome = fail_job(db_session, job.id, error="can never succeed", permanent=True)
    db_session.commit()
    db_session.expire_all()

    assert outcome == "dead"
    refreshed = db_session.get(Job, job.id)
    assert refreshed.status == "dead"
    assert refreshed.attempts == 1
    assert refreshed.last_error == "can never succeed"


def test_a_handler_raising_PermanentJobError_lands_dead_after_one_attempt(db_session, monkeypatch):
    def handler(session, payload):
        raise jobs_module.PermanentJobError("no retry will fix this")

    monkeypatch.setitem(jobs_module._HANDLERS, "perm-boom", handler)
    job = enqueue_job(db_session, job_type="perm-boom", payload={}, max_attempts=5)
    job_id = job.id
    db_session.commit()

    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True

    db_session.expire_all()
    refreshed = db_session.get(Job, job_id)
    assert refreshed.status == "dead"
    assert refreshed.attempts == 1
    assert "no retry will fix this" in refreshed.last_error


def test_a_handler_raising_an_ordinary_error_is_still_retried(db_session, monkeypatch):
    def handler(session, payload):
        raise RuntimeError("transient")

    monkeypatch.setitem(jobs_module._HANDLERS, "flaky-boom", handler)
    job = enqueue_job(db_session, job_type="flaky-boom", payload={}, max_attempts=5)
    job_id = job.id
    db_session.commit()

    jobs_module._run_one_claimed_job(this_worker_id="w1")

    db_session.expire_all()
    refreshed = db_session.get(Job, job_id)
    assert refreshed.status == "queued"
    assert refreshed.attempts == 1


def _transcribe_job_for(db_session, *, fmt):
    from app.db.models import MediaObject
    from app.db.models import User as DbUser

    user = DbUser(name="U", preferred_language="en", role="elder")
    db_session.add(user)
    db_session.flush()
    import uuid

    media = MediaObject(
        id=uuid.uuid4(),
        author_id=user.id,
        format=fmt,
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=1000,
    )
    db_session.add(media)
    db_session.commit()
    return {"media_id": str(media.id)}


def test_transcribe_media_for_a_format_the_ai_contract_lacks_is_permanent(db_session):
    payload = _transcribe_job_for(db_session, fmt="webm_opus")

    with pytest.raises(jobs_module.PermanentJobError, match="webm_opus"):
        jobs_module._handle_transcribe_media(db_session, payload)


def _ai_client_answering(handler):
    """An AiClient whose transport is `handler`, wired in where the handler
    builds its client (Week 7: the call goes through app/ai_client.py)."""
    from app.ai_client import AiClient, AiStage

    return AiClient(
        urls={stage: "http://ai.test" for stage in AiStage},
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_transcribe_media_ai_service_4xx_is_permanent(db_session, monkeypatch):
    payload = _transcribe_job_for(db_session, fmt="wav_pcm16")
    client = _ai_client_answering(lambda request: httpx.Response(404, json={}))
    monkeypatch.setattr(jobs_module, "_ai_client", lambda: client)

    with pytest.raises(jobs_module.PermanentJobError, match="404"):
        jobs_module._handle_transcribe_media(db_session, payload)


def test_transcribe_media_ai_service_5xx_stays_retryable(db_session, monkeypatch):
    from app.ai_client import AiCallError

    payload = _transcribe_job_for(db_session, fmt="wav_pcm16")
    client = _ai_client_answering(lambda request: httpx.Response(503, json={}))
    monkeypatch.setattr(jobs_module, "_ai_client", lambda: client)

    with pytest.raises(AiCallError) as info:
        jobs_module._handle_transcribe_media(db_session, payload)
    assert info.value.retryable


def test_transcribe_media_ai_service_unreachable_stays_retryable(db_session, monkeypatch):
    from app.ai_client import AiCallError

    payload = _transcribe_job_for(db_session, fmt="wav_pcm16")

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    client = _ai_client_answering(refuse)
    monkeypatch.setattr(jobs_module, "_ai_client", lambda: client)

    with pytest.raises(AiCallError) as info:
        jobs_module._handle_transcribe_media(db_session, payload)
    assert info.value.retryable


def _capture_transcribe_request(db_session, monkeypatch, *, fmt):
    """Run the handler against a fake service that returns a valid transcript
    and hand back the JSON body the handler actually sent."""
    sent = {}

    def answer(request):
        import json

        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "text": "hello",
                "detected_language": "en",
                "model_version": "fake",
                "duration_ms": 1.0,
            },
        )

    client = _ai_client_answering(answer)
    monkeypatch.setattr(jobs_module, "_ai_client", lambda: client)
    jobs_module._handle_transcribe_media(db_session, _transcribe_job_for(db_session, fmt=fmt))
    return sent


def test_transcribe_media_sends_a_file_uri_the_real_asr_can_open(db_session, monkeypatch):
    # The real ASR resolves `uri` as a local path / file:// URI; it does not
    # know the gateway's `media:<id>` scheme (Week 7 finding).
    monkeypatch.setattr(get_settings(), "AI_AUDIO_MOUNT_ROOT", "/ai-media")
    sent = _capture_transcribe_request(db_session, monkeypatch, fmt="wav_pcm16")
    assert sent["audio"]["uri"].startswith("file:///ai-media/")
    assert sent["audio"]["uri"].endswith(".bin")


def test_transcribe_media_keeps_the_media_uri_when_no_mount_is_configured(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "AI_AUDIO_MOUNT_ROOT", None)
    sent = _capture_transcribe_request(db_session, monkeypatch, fmt="wav_pcm16")
    assert sent["audio"]["uri"].startswith("media:")


def test_transcribe_media_sends_webm_as_ogg_opus_only_when_opted_in(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "AI_ACCEPT_WEBM_AS_OGG_OPUS", True)
    sent = _capture_transcribe_request(db_session, monkeypatch, fmt="webm_opus")
    assert sent["audio"]["format"] == "ogg_opus"


# -- 5. upload enqueue honors config ------------------------------------------------


def test_upload_does_not_enqueue_a_transcription_job_by_default(client, db_session, login_as):
    from app.db.models import User as DbUser

    alice = DbUser(name="Alice", preferred_language="en", role="elder")
    db_session.add(alice)
    db_session.flush()
    login_as(alice)

    resp = client.post("/media", params={"format": "wav_pcm16"}, content=b"some-audio-bytes")

    assert resp.status_code == 200
    assert db_session.query(Job).count() == 0


def test_upload_enqueues_when_enabled_and_honors_job_max_attempts(
    client, db_session, login_as, monkeypatch
):
    from app.db.models import User as DbUser

    settings = get_settings()
    monkeypatch.setattr(settings, "TRANSCRIBE_ON_UPLOAD_ENABLED", True)
    monkeypatch.setattr(settings, "JOB_MAX_ATTEMPTS", 3)
    alice = DbUser(name="Alice", preferred_language="en", role="elder")
    db_session.add(alice)
    db_session.flush()
    login_as(alice)

    resp = client.post("/media", params={"format": "wav_pcm16"}, content=b"other-audio-bytes")

    assert resp.status_code == 200
    jobs = db_session.query(Job).all()
    assert len(jobs) == 1
    assert jobs[0].job_type == "transcribe_media"
    assert jobs[0].max_attempts == 3
