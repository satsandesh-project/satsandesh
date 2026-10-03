"""Week 7 Phase 5: the pipeline orchestrator (see ORCHESTRATOR_DESIGN.md).

transcribe -> pivot -> moderate -> render, per message, as a durable job. The
tests are organised around the decisions that note asks a reviewer to
challenge: the delivery gate, resumability/idempotency, fail-closed vs
degrade, the action -> status mapping, the languages rendered, and where the
audio comes from. The AI services are scripted (tests/_fake_ai.py) and speak
the real contracts through the real AiClient.

Written before app/pipeline.py exists.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from contracts.ai.moderation import ModerationAction, ModerationLabel
from sqlalchemy import select, text

from app import pipeline
from app.ai_client import AiCallError
from app.config import get_settings
from app.db.models import Job, MediaObject, Message, MessageRendering
from app.db.models import User as DbUser
from app.db.moderation import list_moderation_events
from app.db.repository import (
    add_member,
    create_circle,
    create_media_object,
    create_message,
    set_message_status,
)
from app.jobs import PermanentJobError
from tests._fake_ai import FakeAi

# --- harness -----------------------------------------------------------------------


@pytest.fixture
def fake(tmp_path, monkeypatch):
    ai = FakeAi(audio_dir=tmp_path)
    monkeypatch.setattr(pipeline, "_ai_client", lambda: ai.client())
    monkeypatch.setattr(get_settings(), "AI_RENDER_AUDIO_ROOT", str(tmp_path))
    monkeypatch.setattr(get_settings(), "AI_AUDIO_MOUNT_ROOT", None)
    monkeypatch.setattr(get_settings(), "PIPELINE_NUDGE_DELIVERS", True)
    return ai


@pytest.fixture
def outbox(monkeypatch):
    """Records what the pipeline asks the event loop to do, instead of doing it."""
    box = {"delivery": [], "status": []}
    monkeypatch.setattr(pipeline, "_request_delivery", lambda mid: box["delivery"].append(mid))
    monkeypatch.setattr(
        pipeline, "_notify_author", lambda mid, status: box["status"].append((mid, status))
    )
    return box


def _user(db_session, name, language="te", role="elder"):
    user = DbUser(name=name, preferred_language=language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _dm(
    db_session, *, author_lang="te", target_lang="hi", kind="text", due=False, source_lang="te"
):
    author = _user(db_session, "Author", author_lang)
    target = _user(db_session, "Target", target_lang)
    return _message(
        db_session, author, target_user=target, kind=kind, due=due, source_lang=source_lang
    )


def _message(
    db_session,
    author,
    *,
    target_user=None,
    target_circle=None,
    kind="text",
    due=False,
    source_lang="te",
):
    extra = {}
    if kind == "voice":
        media, _ = create_media_object(
            db_session,
            media_id=uuid.uuid4(),
            author_id=author.id,
            format="wav_pcm16",
            sha256_hex=uuid.uuid4().hex,
            size_bytes=10,
            duration_ms=4200,
        )
        extra = {
            "original_media_ref": f"media:{media.id}",
            "media_object_id": media.id,
            "media_format": "wav_pcm16",
            "media_duration_ms": 4200,
        }
    expires = datetime.now(UTC) + (timedelta(seconds=-5) if due else timedelta(seconds=300))
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user" if target_user else "circle",
        target_user_id=target_user.id if target_user else None,
        target_circle_id=target_circle.id if target_circle else None,
        kind=kind,
        text="ఈ రోజు సత్సంగం ఎప్పుడు?" if kind == "text" else None,
        source_lang=source_lang,
        client_msg_id=uuid.uuid4(),
        undo_expires_at=expires,
        **extra,
    )
    pipeline.start_pipeline(db_session, message)
    db_session.commit()
    return message.id


def _run(db_session, message_id):
    pipeline.handle_process_message(db_session, {"message_id": str(message_id)})
    db_session.commit()
    db_session.expire_all()


def _msg(db_session, message_id) -> Message:
    db_session.expire_all()
    return db_session.get(Message, message_id)


def _renderings(db_session, message_id):
    return {
        r.language: r
        for r in db_session.scalars(
            select(MessageRendering).where(MessageRendering.message_id == message_id)
        )
    }


# --- starting the pipeline ----------------------------------------------------------------


def test_starting_the_pipeline_marks_the_message_and_enqueues_its_job_together(db_session):
    message_id = _dm(db_session)

    assert _msg(db_session, message_id).pipeline_state == "pending"
    jobs = db_session.scalars(select(Job).where(Job.job_type == "process_message")).all()
    assert [j.payload for j in jobs] == [{"message_id": str(message_id)}]


def test_the_job_honours_the_configured_max_attempts(db_session, monkeypatch):
    monkeypatch.setattr(get_settings(), "JOB_MAX_ATTEMPTS", 3)
    _dm(db_session)
    (job,) = db_session.scalars(select(Job)).all()
    assert job.max_attempts == 3


# --- the happy path -------------------------------------------------------------------------


def test_a_text_message_goes_pivot_then_moderate_then_render(db_session, fake, outbox):
    message_id = _dm(db_session, target_lang="hi")

    _run(db_session, message_id)

    assert fake.stages() == ["pivot", "moderate", "render"]
    assert fake.requests("pivot")[0]["source_language"] == "te"
    # The classifier reads the English pivot, not the original.
    assert fake.requests("moderate")[0]["text"] == "When is satsang today?"
    assert fake.requests("render")[0]["target_languages"] == ["hi"]
    assert fake.requests("render")[0]["pivot_text"] == "When is satsang today?"


def test_every_stage_output_is_stored(db_session, fake, outbox):
    message_id = _dm(db_session, target_lang="hi")

    _run(db_session, message_id)

    message = _msg(db_session, message_id)
    assert message.pivot_text_en == "When is satsang today?"
    assert message.pipeline_state == "complete"
    (event,) = list_moderation_events(db_session, message_id)
    assert (event.actor_kind, event.action, event.degraded) == ("classifier", "ALLOW", False)
    rendering = _renderings(db_session, message_id)["hi"]
    assert rendering.text == "[hi] When is satsang today?"
    assert rendering.model_version_tts == "fake-tts:hi"


def test_rendering_audio_is_ingested_from_the_shared_directory_by_file_name(
    db_session, fake, outbox, tmp_path
):
    message_id = _dm(db_session, target_lang="hi")

    _run(db_session, message_id)

    rendering = _renderings(db_session, message_id)["hi"]
    media = db_session.get(MediaObject, rendering.audio_media_object_id)
    author_id = _msg(db_session, message_id).author_id
    assert media.author_id == author_id, "owned by the message's author"
    assert media.format == "wav_pcm16"
    # The fake reported a path under /render/output/...; the file was read
    # from AI_RENDER_AUDIO_ROOT by name.
    from app.media_storage import get_media_storage

    assert get_media_storage().get(str(media.id)) == (tmp_path / "render-1-hi.wav").read_bytes()


def test_a_voice_note_is_transcribed_first_and_the_transcript_stored(db_session, fake, outbox):
    fake.transcript = ("ఈ రోజు సత్సంగం ఎప్పుడు?", "te")
    message_id = _dm(db_session, kind="voice", source_lang=None)

    _run(db_session, message_id)

    assert fake.stages() == ["transcribe", "pivot", "moderate", "render"]
    message = _msg(db_session, message_id)
    assert (message.transcript, message.transcript_language) == ("ఈ రోజు సత్సంగం ఎప్పుడు?", "te")
    # The pivot translates the transcript, in the language ASR detected.
    assert fake.requests("pivot")[0]["text"] == "ఈ రోజు సత్సంగం ఎప్పుడు?"
    assert fake.requests("pivot")[0]["source_language"] == "te"


def test_the_asr_is_given_the_authors_language_as_a_hint(db_session, fake, outbox):
    message_id = _dm(db_session, author_lang="hi", kind="voice", source_lang=None)

    _run(db_session, message_id)

    assert fake.requests("transcribe")[0]["language_hint"] == "hi"


# --- delivery gate (decision 1 and 2) ----------------------------------------------------------


def test_delivery_is_requested_only_if_the_undo_window_has_already_elapsed(
    db_session, fake, outbox
):
    inside = _dm(db_session, due=False)
    after = _dm(db_session, due=True)

    _run(db_session, inside)
    assert outbox["delivery"] == [], "the scheduled fan-out will deliver this one"
    _run(db_session, after)
    assert outbox["delivery"] == [after]


def test_the_state_is_committed_complete_before_delivery_is_requested(
    db_session, fake, outbox, engine, monkeypatch
):
    """The no-lost-wake-up argument (ORCHESTRATOR_DESIGN.md #2) depends on
    this order: 'complete' must be durable BEFORE the clock is read, so a
    fan-out that skipped while it was 'pending' is always followed by this
    delivery."""
    from sqlalchemy.orm import Session

    seen = {}

    def request_delivery(message_id):
        with Session(engine) as other:
            seen["state"] = other.scalar(
                text("select pipeline_state from messages where id = :i"), {"i": message_id}
            )

    monkeypatch.setattr(pipeline, "_request_delivery", request_delivery)
    message_id = _dm(db_session, due=True)

    _run(db_session, message_id)

    assert seen["state"] == "complete"


async def test_fan_out_waits_while_the_pipeline_is_pending_and_delivers_once_complete(
    db_session, engine, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    import app.ws as ws_module
    from app.messages import fan_out_message

    sent = []

    async def capture(recipients, frame, exclude=None):
        sent.append(frame)

    monkeypatch.setattr(ws_module.manager, "broadcast", capture)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    message_id = _dm(db_session, due=True)  # pipeline_state = 'pending'

    await fan_out_message(message_id, ws_module.manager, factory)
    assert _msg(db_session, message_id).status == "pending"
    assert sent == []

    db_session.execute(
        text("update messages set pipeline_state = 'complete' where id = :i"), {"i": message_id}
    )
    db_session.commit()
    await fan_out_message(message_id, ws_module.manager, factory)
    assert _msg(db_session, message_id).status == "sent"


async def test_a_message_with_no_pipeline_is_delivered_exactly_as_before(
    db_session, engine, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    import app.ws as ws_module
    from app.messages import fan_out_message

    async def capture(recipients, frame, exclude=None):
        return None

    monkeypatch.setattr(ws_module.manager, "broadcast", capture)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    author, target = _user(db_session, "A"), _user(db_session, "B")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="text",
        text="hello",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    assert message.pipeline_state is None

    await fan_out_message(message.id, ws_module.manager, factory)

    assert _msg(db_session, message.id).status == "sent"


# --- resumable and idempotent (decision 3) ---------------------------------------------------------


def test_a_retry_resumes_after_the_stage_that_failed_and_does_not_redo_earlier_ones(
    db_session, fake, outbox
):
    message_id = _dm(db_session, kind="voice", source_lang=None)
    fake.errors["moderate"] = (503, {"code": "MODEL_LOAD_FAILED", "message": "loading"})

    with pytest.raises(AiCallError) as info:
        pipeline.handle_process_message(db_session, {"message_id": str(message_id)})
    assert info.value.retryable
    db_session.commit()
    assert fake.stages() == ["transcribe", "pivot", "moderate"]
    stored = _msg(db_session, message_id)
    assert stored.transcript and stored.pivot_text_en
    assert stored.pipeline_state == "pending" and stored.status == "pending"

    fake.errors.clear()
    fake.calls.clear()
    _run(db_session, message_id)

    assert fake.stages() == ["moderate", "render"], "transcribe and pivot must not be redone"
    assert _msg(db_session, message_id).pipeline_state == "complete"


def test_running_a_finished_message_again_changes_and_calls_nothing(db_session, fake, outbox):
    message_id = _dm(db_session)
    _run(db_session, message_id)
    fake.calls.clear()

    _run(db_session, message_id)

    assert fake.calls == []
    assert len(list_moderation_events(db_session, message_id)) == 1
    assert len(_renderings(db_session, message_id)) == 1


def test_a_message_undone_before_the_job_runs_is_abandoned(db_session, fake, outbox):
    message_id = _dm(db_session)
    set_message_status(db_session, message_id, new_status="cancelled", expected="pending")
    db_session.commit()

    _run(db_session, message_id)

    assert fake.calls == []
    assert list_moderation_events(db_session, message_id) == []


# --- which languages (decision 6) --------------------------------------------------------------------


def test_a_circle_is_rendered_into_its_members_languages_minus_the_senders_and_unsupported(
    db_session, fake, outbox
):
    author = _user(db_session, "Author", "te")
    circle = create_circle(db_session, name="Satsang", created_by=author.id)
    add_member(db_session, circle_id=circle.id, user_id=author.id, role="admin")
    for name, language in [("A", "hi"), ("B", "hi"), ("C", "en"), ("D", "te"), ("E", "ta")]:
        add_member(db_session, circle_id=circle.id, user_id=_user(db_session, name, language).id)
    message_id = _message(db_session, author, target_circle=circle)

    _run(db_session, message_id)

    # hi twice -> once; te is the message's own language; ta cannot be rendered.
    assert fake.requests("render")[0]["target_languages"] == ["en", "hi"]
    assert set(_renderings(db_session, message_id)) == {"en", "hi"}


def test_nothing_is_rendered_when_every_recipient_already_speaks_the_senders_language(
    db_session, fake, outbox
):
    message_id = _dm(db_session, author_lang="te", target_lang="te", source_lang="te")

    _run(db_session, message_id)

    assert fake.stages() == ["pivot", "moderate"]
    assert _renderings(db_session, message_id) == {}
    assert _msg(db_session, message_id).pipeline_state == "complete"


def test_a_regional_source_language_is_reduced_to_its_primary_subtag(db_session, fake, outbox):
    message_id = _dm(db_session, source_lang="te-IN")

    _run(db_session, message_id)

    assert fake.requests("pivot")[0]["source_language"] == "te"


# --- moderation outcomes (decision 5) ------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,status",
    [(ModerationAction.HOLD, "held"), (ModerationAction.BLOCK, "blocked")],
)
def test_a_hold_or_block_verdict_changes_the_status_and_nothing_is_delivered(
    db_session, fake, outbox, action, status
):
    fake.action = action
    fake.label = ModerationLabel.D_DISPUTATIONAL
    message_id = _dm(db_session, due=True)

    _run(db_session, message_id)

    assert _msg(db_session, message_id).status == status
    assert outbox["delivery"] == []
    assert outbox["status"] == [(message_id, status)]
    (event,) = list_moderation_events(db_session, message_id)
    assert event.action == action.value


def test_a_held_message_still_gets_its_renderings_so_a_release_needs_no_rerun(
    db_session, fake, outbox
):
    fake.action = ModerationAction.HOLD
    message_id = _dm(db_session, target_lang="hi")

    _run(db_session, message_id)

    assert set(_renderings(db_session, message_id)) == {"hi"}


def test_a_degraded_fallback_hold_is_recorded_as_a_degraded_classifier_event(
    db_session, fake, outbox
):
    fake.action = ModerationAction.HOLD
    fake.degraded_verdict = True
    message_id = _dm(db_session)

    _run(db_session, message_id)

    (event,) = list_moderation_events(db_session, message_id)
    assert event.degraded is True and event.degraded_reason == "model_fallback"
    assert _msg(db_session, message_id).status == "held"


def test_a_nudge_delivers_by_default_and_records_the_nudge(db_session, fake, outbox):
    fake.action = ModerationAction.NUDGE
    fake.label = ModerationLabel.C_PERSONAL
    fake.nudge_text = "Please be gentle."
    message_id = _dm(db_session, due=True)

    _run(db_session, message_id)

    assert outbox["delivery"] == [message_id]
    (event,) = list_moderation_events(db_session, message_id)
    assert event.action == "NUDGE"


def test_a_nudge_can_be_configured_to_hold_instead(db_session, fake, outbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "PIPELINE_NUDGE_DELIVERS", False)
    fake.action = ModerationAction.NUDGE
    fake.nudge_text = "Please be gentle."
    message_id = _dm(db_session, due=True)

    _run(db_session, message_id)

    assert _msg(db_session, message_id).status == "held"
    assert outbox["delivery"] == []


# --- the sender's notice (decision 8) ----------------------------------------------------------------------


def test_a_non_allow_notice_is_translated_into_the_senders_language_and_recorded(
    db_session, fake, outbox
):
    fake.action = ModerationAction.HOLD
    fake.nudge_text = "Held for review."
    message_id = _dm(db_session, author_lang="te", target_lang="hi")

    _run(db_session, message_id)

    notice_requests = [r for r in fake.requests("render") if r["pivot_text"] == "Held for review."]
    assert [r["target_languages"] for r in notice_requests] == [["te"]]
    (event,) = list_moderation_events(db_session, message_id)
    assert event.notice_text == "[te] Held for review."


def test_a_failed_notice_translation_records_no_notice_rather_than_the_english_one(
    db_session, fake, outbox
):
    fake.action = ModerationAction.HOLD
    fake.nudge_text = "Held for review."
    # Only the notice render fails; the message's own renderings succeed.
    original = fake._render

    def render(body):
        if body["pivot_text"] == "Held for review.":
            raise httpx.ConnectError("render down")
        return original(body)

    fake._render = render
    message_id = _dm(db_session)

    _run(db_session, message_id)

    (event,) = list_moderation_events(db_session, message_id)
    assert event.notice_text is None
    assert _msg(db_session, message_id).status == "held"


# --- fail closed (decision 4) -----------------------------------------------------------------------------


def test_an_unreachable_moderation_service_is_retried_and_nothing_is_delivered(
    db_session, fake, outbox
):
    fake.errors["moderate"] = httpx.ConnectError("refused")
    message_id = _dm(db_session, due=True)

    with pytest.raises(AiCallError) as info:
        pipeline.handle_process_message(db_session, {"message_id": str(message_id)})

    assert info.value.retryable
    db_session.commit()
    assert _msg(db_session, message_id).status == "pending"
    assert _msg(db_session, message_id).pipeline_state == "pending"
    assert outbox["delivery"] == []


def test_a_request_the_asr_rejects_is_permanent(db_session, fake, outbox):
    fake.errors["transcribe"] = (422, {"code": "AUDIO_FETCH_FAILED", "message": "no file"})
    message_id = _dm(db_session, kind="voice", source_lang=None)

    with pytest.raises(PermanentJobError):
        pipeline.handle_process_message(db_session, {"message_id": str(message_id)})


def test_a_silent_note_is_held_for_a_human_not_delivered_unclassified(db_session, fake, outbox):
    fake.transcript = ("   ", "te")
    message_id = _dm(db_session, kind="voice", source_lang=None, due=True)

    _run(db_session, message_id)

    message = _msg(db_session, message_id)
    assert message.status == "held" and message.pipeline_state == "complete"
    (event,) = list_moderation_events(db_session, message_id)
    assert (event.actor_kind, event.action) == ("system", "HOLD")
    assert "speech" in event.rationale.lower()
    assert outbox["delivery"] == []
    assert fake.stages() == ["transcribe"], "an empty transcript cannot be translated or ruled on"


def test_a_language_the_pipeline_cannot_handle_is_held(db_session, fake, outbox):
    message_id = _dm(db_session, author_lang="ta", source_lang="ta")

    with pytest.raises(PermanentJobError, match="ta"):
        pipeline.handle_process_message(db_session, {"message_id": str(message_id)})


def test_when_the_job_dies_before_a_verdict_the_message_is_held_with_a_system_event(
    db_session, fake, outbox
):
    message_id = _dm(db_session, due=True)

    pipeline.handle_pipeline_dead(db_session, {"message_id": str(message_id)}, "ASR down for good")
    db_session.commit()

    message = _msg(db_session, message_id)
    assert (message.status, message.pipeline_state) == ("held", "failed")
    (event,) = list_moderation_events(db_session, message_id)
    assert (event.actor_kind, event.action) == ("system", "HOLD")
    assert "ASR down for good" in event.rationale
    assert outbox["status"] == [(message_id, "held")]
    assert outbox["delivery"] == []


def test_when_the_job_dies_after_an_allow_the_message_is_delivered_without_renderings(
    db_session, fake, outbox
):
    """Translations are an enhancement; moderation is the gate. A render
    service that stays down must not strand a message moderation approved."""
    fake.errors["render"] = (503, {"code": "MODEL_LOAD_FAILED", "message": "loading"})
    message_id = _dm(db_session, due=True)
    with pytest.raises(AiCallError):
        pipeline.handle_process_message(db_session, {"message_id": str(message_id)})
    db_session.commit()

    pipeline.handle_pipeline_dead(db_session, {"message_id": str(message_id)}, "render down")
    db_session.commit()

    message = _msg(db_session, message_id)
    assert message.status == "pending" and message.pipeline_state == "complete"
    assert outbox["delivery"] == [message_id]
    assert len(list_moderation_events(db_session, message_id)) == 1, "no hold event added"


def test_the_dead_hook_leaves_a_message_that_is_already_out_alone(db_session, fake, outbox):
    message_id = _dm(db_session)
    set_message_status(db_session, message_id, new_status="sent", expected="pending")
    db_session.commit()

    pipeline.handle_pipeline_dead(db_session, {"message_id": str(message_id)}, "late")
    db_session.commit()

    message = _msg(db_session, message_id)
    assert message.status == "sent" and message.pipeline_state == "failed"
    assert list_moderation_events(db_session, message_id) == []


# --- rendering audio (decision 7) ---------------------------------------------------------------------------


def test_a_rendering_whose_audio_file_cannot_be_read_keeps_its_text(db_session, fake, outbox):
    fake.write_audio_files = False
    message_id = _dm(db_session, target_lang="hi")

    _run(db_session, message_id)

    rendering = _renderings(db_session, message_id)["hi"]
    assert rendering.text == "[hi] When is satsang today?"
    assert rendering.audio_media_object_id is None
    assert rendering.degraded_reason == "tts_skipped"
    assert _msg(db_session, message_id).pipeline_state == "complete"


def test_a_language_with_no_voice_is_stored_as_text_only(db_session, fake, outbox):
    fake.tts_languages = {"hi"}
    author = _user(db_session, "Author", "te")
    target = _user(db_session, "Target", "en")
    message_id = _message(db_session, author, target_user=target)

    _run(db_session, message_id)

    rendering = _renderings(db_session, message_id)["en"]
    assert rendering.audio_media_object_id is None
    assert rendering.degraded_reason == "text_only"


def test_oversized_rendering_audio_is_not_ingested(db_session, fake, outbox, monkeypatch):
    monkeypatch.setattr(get_settings(), "MEDIA_MAX_UPLOAD_BYTES", 10)
    message_id = _dm(db_session, target_lang="hi")

    _run(db_session, message_id)

    rendering = _renderings(db_session, message_id)["hi"]
    assert rendering.audio_media_object_id is None
    assert rendering.degraded_reason == "tts_skipped"


# --- through the real worker --------------------------------------------------------------------------------


def test_a_permanent_failure_in_the_worker_holds_the_message(db_session, fake, outbox):
    import app.jobs as jobs_module

    fake.errors["pivot"] = (422, {"code": "UNSUPPORTED_LANGUAGE", "message": "no"})
    message_id = _dm(db_session)

    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True

    message = _msg(db_session, message_id)
    assert (message.status, message.pipeline_state) == ("held", "failed")
    (job,) = db_session.scalars(select(Job)).all()
    db_session.refresh(job)
    assert job.status == "dead"


def test_exhausting_retries_in_the_worker_holds_the_message(db_session, fake, outbox, monkeypatch):
    import app.jobs as jobs_module

    monkeypatch.setattr(get_settings(), "JOB_MAX_ATTEMPTS", 1)
    fake.errors["pivot"] = httpx.ConnectError("refused")
    message_id = _dm(db_session)

    jobs_module._run_one_claimed_job(this_worker_id="w1")

    message = _msg(db_session, message_id)
    assert (message.status, message.pipeline_state) == ("held", "failed")


def test_a_transient_failure_in_the_worker_leaves_the_message_waiting(
    db_session, fake, outbox, monkeypatch
):
    import app.jobs as jobs_module

    fake.errors["pivot"] = httpx.ConnectError("refused")
    message_id = _dm(db_session)

    jobs_module._run_one_claimed_job(this_worker_id="w1")

    message = _msg(db_session, message_id)
    assert (message.status, message.pipeline_state) == ("pending", "pending")
    (job,) = db_session.scalars(select(Job)).all()
    db_session.refresh(job)
    assert job.status == "queued" and job.attempts == 1


# --- wiring into message creation ------------------------------------------------------------------------------


def _post(client, login_as, author, target, **extra):
    login_as(author)
    body = {
        "client_msg_id": extra.pop("client_msg_id", str(uuid.uuid4())),
        "target_type": "user",
        "target_id": str(target.id),
        "kind": "text",
        "text": "hello",
        "source_lang": "te",
    }
    return client.post("/messages", json=body)


def test_creating_a_message_does_nothing_pipeline_related_by_default(client, db_session, login_as):
    author, target = _user(db_session, "A"), _user(db_session, "B", "hi")

    resp = _post(client, login_as, author, target)

    assert resp.status_code == 200
    assert db_session.scalars(select(Job)).all() == []
    message = db_session.get(Message, uuid.UUID(resp.json()["id"]))
    assert message.pipeline_state is None


def test_creating_a_message_with_the_pipeline_on_starts_it(
    client, db_session, login_as, monkeypatch
):
    monkeypatch.setattr(get_settings(), "PIPELINE_ENABLED", True)
    author, target = _user(db_session, "A"), _user(db_session, "B", "hi")

    resp = _post(client, login_as, author, target)

    message_id = uuid.UUID(resp.json()["id"])
    db_session.expire_all()
    assert db_session.get(Message, message_id).pipeline_state == "pending"
    (job,) = db_session.scalars(select(Job).where(Job.job_type == "process_message")).all()
    assert job.payload == {"message_id": str(message_id)}


def test_a_retried_send_does_not_start_a_second_pipeline(client, db_session, login_as, monkeypatch):
    monkeypatch.setattr(get_settings(), "PIPELINE_ENABLED", True)
    author, target = _user(db_session, "A"), _user(db_session, "B", "hi")
    same = str(uuid.uuid4())

    _post(client, login_as, author, target, client_msg_id=same)
    _post(client, login_as, author, target, client_msg_id=same)

    assert len(db_session.scalars(select(Job)).all()) == 1


def test_a_websocket_send_with_the_pipeline_on_starts_it_too(
    client, db_session, ws_login_as, monkeypatch
):
    monkeypatch.setattr(get_settings(), "PIPELINE_ENABLED", True)
    author, target = _user(db_session, "A"), _user(db_session, "B", "hi")
    token = ws_login_as(author)

    with client.websocket_connect(f"/ws?token={token}") as ws:
        ws.send_json(
            {
                "type": "message.send",
                "data": {
                    "client_msg_id": str(uuid.uuid4()),
                    "target_type": "user",
                    "target_id": str(target.id),
                    "kind": "text",
                    "text": "hello",
                    "source_lang": "te",
                },
            }
        )
        ack = ws.receive_json()

    message_id = uuid.UUID(ack["data"]["id"])
    db_session.expire_all()
    assert db_session.get(Message, message_id).pipeline_state == "pending"
    assert len(db_session.scalars(select(Job)).all()) == 1
