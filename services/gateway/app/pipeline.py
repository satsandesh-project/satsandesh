"""The pipeline orchestrator: transcribe -> pivot -> moderate -> render, per
message, as a durable job. Read ORCHESTRATOR_DESIGN.md first -- it states the
decisions this module implements and why.

The short version of the one that matters most: delivery must WAIT for the
pipeline. `app/messages.py::fan_out_message` flips `pending -> sent` when the
undo window ends and does not look at the pipeline, so a pipeline that merely
added a job would deliver a message with no renderings whenever CPU inference
outlasted the 30s window -- and Phase 4's fixed-at-delivery rule would then
forbid them ever arriving. `messages.pipeline_state` is the gate:
`start_pipeline` sets it `pending` in the SAME transaction as the message and
its job; `fan_out_message` waits while it is `pending`; the handler's last
steps (commit `complete`, THEN read the clock, deliver if the window has
already elapsed) mean exactly one of the scheduled fan-out and the job always
delivers.

Runs in the job worker's thread (app/jobs.py), against the handler's own
session. Each stage commits its output, so a retry resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import unquote, urlparse

from contracts.ai.language import LanguageCode
from contracts.ai.moderation import ModerationAction, ModerationDecision, ModerationRequest
from contracts.ai.pivot import PivotRequest
from contracts.ai.render import RenderRequest, RenderResult
from contracts.ai.transcribe import TranscribeRequest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.ai_audio import UnsupportedAudioFormatError, ai_audio_ref
from app.ai_client import AiCallError, AiClient
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models import Membership, Message, MessageRendering, User
from app.db.moderation import (
    latest_moderation_event,
    record_classifier_decision,
    record_moderation_event,
)
from app.db.renderings import set_message_pivot_text, set_message_transcript, upsert_rendering
from app.db.repository import (
    create_media_object,
    enqueue_job,
    find_media_object,
    get_media_object,
    set_message_status,
)
from app.jobs import PermanentJobError
from app.media_storage import get_media_storage

logger = logging.getLogger(__name__)

JOB_TYPE = "process_message"

SUPPORTED_LANGUAGES = frozenset(language.value for language in LanguageCode)

# A SYSTEM event has to carry a policy_version (the column is NOT NULL); this
# names what produced it, since no policy document ruled on the message.
_SYSTEM_POLICY_VERSION = "pipeline"
# The AI service's own placeholder for "unclassified -- held" (decide.py).
_UNCLASSIFIED_LABEL = "D_DISPUTATIONAL"

_NO_AUDIO_REASONS = ("text_only", "tts_skipped")


# --- starting it --------------------------------------------------------------------


def start_pipeline(session: Session, message: Message) -> None:
    """Mark the message's pipeline `pending` and enqueue its job -- both in
    the caller's transaction, so there is no moment in which a message exists
    without its job, or a job without the gate that waits for it. The caller
    commits."""
    message.pipeline_state = "pending"
    session.flush()
    enqueue_job(
        session,
        job_type=JOB_TYPE,
        payload={"message_id": str(message.id)},
        max_attempts=get_settings().JOB_MAX_ATTEMPTS,
    )


# --- seams for the event loop (the worker is a thread; delivery is async) ---------------


def _ai_client() -> AiClient:
    return AiClient.from_settings(get_settings())


def _run_on_main_loop(coro_factory) -> None:
    """Hand a coroutine to the gateway's event loop (captured by the worker
    loop, app/jobs.py). Fire-and-forget: failures are logged, not raised into
    the job -- delivery is idempotent and recovery re-schedules at restart."""
    import app.jobs as jobs_module

    loop = jobs_module._MAIN_LOOP
    if loop is None or loop.is_closed():
        logger.warning("no event loop to run %s on; recovery will pick it up", coro_factory)
        return
    future = asyncio.run_coroutine_threadsafe(coro_factory(), loop)

    def _log(done) -> None:
        if done.exception():
            logger.error("pipeline follow-up failed: %r", done.exception())

    future.add_done_callback(_log)


def _request_delivery(message_id: uuid.UUID) -> None:
    """Ask the event loop to deliver now (the undo window has already elapsed).
    fan_out_message's atomic pending -> sent makes a double trigger harmless."""

    async def deliver() -> None:
        from app.messages import fan_out_message
        from app.ws import manager

        await fan_out_message(message_id, manager, SessionLocal)

    _run_on_main_loop(deliver)


def _notify_author(message_id: uuid.UUID, status: str) -> None:
    """Tell the sender's connected devices their message's status changed, so
    a held or blocked message does not just sit on a 'pending' tick."""

    async def notify() -> None:
        from contracts.chat.common import MessageStatus
        from contracts.chat.envelope import FrameType
        from contracts.chat.messages import MessageStatusOut

        from app.ws import manager

        with SessionLocal() as session:
            author_id = session.scalar(select(Message.author_id).where(Message.id == message_id))
        if author_id is None:
            return
        frame = {
            "type": FrameType.MESSAGE_STATUS.value,
            "data": MessageStatusOut(id=str(message_id), status=MessageStatus(status)).model_dump(
                mode="json"
            ),
        }
        await manager.broadcast([str(author_id)], frame)

    _run_on_main_loop(notify)


# --- the job ----------------------------------------------------------------------------


def handle_process_message(session: Session, payload: dict) -> None:
    message_id = uuid.UUID(payload["message_id"])
    message = session.get(Message, message_id)
    # Nothing to do for a message that is gone, whose pipeline already finished,
    # or that was undone/cancelled (writes are guarded to `pending` anyway).
    # held/blocked with a still-pending pipeline is a crash between applying a
    # verdict and recording completion: resume and finish.
    if (
        message is None
        or message.pipeline_state != "pending"
        or message.status not in ("pending", "held", "blocked")
    ):
        return

    client = _ai_client()
    try:
        _run_stages(session, client, message)
    finally:
        client.close()


def _primary_subtag(code: str | None) -> str | None:
    if not code:
        return None
    return code.replace("_", "-").split("-")[0].lower() or None


def _source_language(message: Message, author: User) -> str | None:
    """The language the message was written in. An explicitly declared
    `source_lang` the pipeline cannot handle is an error (translating it as
    something else would mistranslate); an absent one falls back to the
    author's own language."""
    declared = _primary_subtag(message.source_lang)
    if declared is not None:
        if declared not in SUPPORTED_LANGUAGES:
            raise PermanentJobError(
                f"message language {declared!r} is not one the pipeline supports "
                f"({sorted(SUPPORTED_LANGUAGES)})"
            )
        return declared
    fallback = _primary_subtag(author.preferred_language)
    if fallback in SUPPORTED_LANGUAGES:
        return fallback
    return None


def _fail_or_retry(exc: AiCallError) -> NoReturn:
    """Retryable -> let the worker back off; anything else cannot be fixed by
    retrying the identical request."""
    if exc.retryable:
        raise exc
    raise PermanentJobError(str(exc)) from exc


def _run_stages(session: Session, client: AiClient, message: Message) -> None:
    message_id = message.id
    author = session.get(User, message.author_id)
    settings = get_settings()

    # -- already ruled on (a resumed run): skip straight to finishing ----------------
    event = latest_moderation_event(session, message_id)
    if event is None or event.actor_kind != "classifier":
        source_language: str | None = None

        # -- 1. transcribe (voice only) -------------------------------------------------
        if message.kind == "voice":
            if message.transcript is None:
                media = (
                    get_media_object(session, message.media_object_id)
                    if message.media_object_id
                    else None
                )
                if media is None:
                    raise PermanentJobError("voice message has no live audio to transcribe")
                try:
                    audio = ai_audio_ref(
                        media,
                        mount_root=settings.AI_AUDIO_MOUNT_ROOT,
                        webm_as_ogg_opus=settings.AI_ACCEPT_WEBM_AS_OGG_OPUS,
                    )
                except UnsupportedAudioFormatError as exc:
                    raise PermanentJobError(str(exc)) from exc
                # The hint is what the sender DECLARED, never their stored
                # language: that is what they want to RECEIVE (Telugu for every
                # user today), and forcing it would push a Hindi or English
                # note through a Telugu transcription. Undeclared (or a
                # declaration the pipeline cannot use) -> no hint; the ASR
                # detects, and the transcript's own language is what the rest
                # of the pipeline uses.
                declared = _primary_subtag(message.source_lang)
                hint = LanguageCode(declared) if declared in SUPPORTED_LANGUAGES else None
                try:
                    transcribed = client.transcribe(
                        TranscribeRequest(audio=audio, language_hint=hint)
                    )
                except AiCallError as exc:
                    _fail_or_retry(exc)
                transcript = transcribed.text.strip()
                if not transcript:
                    # Nothing to translate or rule on. A person decides --
                    # an unclassified note is never delivered.
                    _hold_for_pipeline(
                        session, message, "No speech was recognised in the voice note.", "complete"
                    )
                    return
                if not set_message_transcript(
                    session, message_id, transcript, transcribed.detected_language.value
                ):
                    return  # no longer pending: undone mid-pipeline
                session.commit()
                session.refresh(message)
            source_text = message.transcript
            source_language = message.transcript_language
        else:
            # Typed text has no second chance to detect its language, so an
            # undeclared one falls back to the author's stored language -- weak
            # (an old client that sends no source_lang), and documented.
            source_language = _source_language(message, author)
            source_text = message.text

        # -- 2. pivot ------------------------------------------------------------------------
        if message.pivot_text_en is None:
            if source_language is None:
                raise PermanentJobError(
                    "cannot translate: no supported language for the message or its author"
                )
            try:
                pivoted = client.pivot(
                    PivotRequest(text=source_text, source_language=LanguageCode(source_language))
                )
            except AiCallError as exc:
                _fail_or_retry(exc)
            pivot_text = pivoted.pivot_text.strip()
            if not pivot_text:
                _hold_for_pipeline(
                    session, message, "The message translated to nothing.", "complete"
                )
                return
            if not set_message_pivot_text(session, message_id, pivot_text):
                return
            session.commit()
            session.refresh(message)

        # -- 3. moderate --------------------------------------------------------------------------
        try:
            decision = client.moderate(ModerationRequest(text=message.pivot_text_en))
        except AiCallError as exc:
            _fail_or_retry(exc)
        notice = None
        if decision.action is not ModerationAction.ALLOW and decision.nudge_text:
            notice = _translate_notice(client, decision, author)
        event = record_classifier_decision(
            session, message_id=message_id, decision=decision, notice_text=notice
        )
        session.commit()

    # -- 4. render (always, so a later moderator release needs no re-run) -----------------
    source_language = (
        message.transcript_language
        if message.kind == "voice"
        else _source_language(message, author)
    )
    _render_missing(session, client, message, source_language)

    # -- 5. finish -----------------------------------------------------------------------------
    _finish(session, message, event.action, state="complete")


def _target_languages(session: Session, message: Message, source_language: str | None) -> list[str]:
    """The languages the RECIPIENTS read (M1's answer on #83: render only
    those), restricted to what the pipeline can render, minus the message's
    own language -- a receiver in the sender's language reads the original."""
    if message.target_type == "user":
        raw = [
            session.scalar(select(User.preferred_language).where(User.id == message.target_user_id))
        ]
    else:
        raw = list(
            session.scalars(
                select(User.preferred_language)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    Membership.circle_id == message.target_circle_id, User.id != message.author_id
                )
            )
        )
    wanted = {_primary_subtag(language) for language in raw}
    return sorted(
        language
        for language in wanted
        if language in SUPPORTED_LANGUAGES and language != source_language
    )


def _render_missing(
    session: Session, client: AiClient, message: Message, source_language: str | None
) -> None:
    targets = _target_languages(session, message, source_language)
    existing = set(
        session.scalars(
            select(MessageRendering.language).where(MessageRendering.message_id == message.id)
        )
    )
    missing = [language for language in targets if language not in existing]
    if not missing:
        return
    try:
        rendered = client.render(
            RenderRequest(
                pivot_text=message.pivot_text_en,
                target_languages=[LanguageCode(language) for language in missing],
            )
        )
    except AiCallError as exc:
        _fail_or_retry(exc)
    for result in rendered.results:
        language = result.language.value
        text = result.text.strip()
        if language not in missing or not text:
            continue
        audio_id, reason = _rendering_audio(session, message, result)
        written = upsert_rendering(
            session,
            message_id=message.id,
            language=language,
            text=text,
            audio_media_object_id=audio_id,
            degraded_reason=reason,
            model_version_translate=result.model_version_translate,
            model_version_tts=result.model_version_tts,
        )
        if not written:
            return  # no longer pending
    session.commit()


def _rendering_audio(
    session: Session, message: Message, result: RenderResult
) -> tuple[uuid.UUID | None, str | None]:
    """(media id, degraded_reason) for one rendering. A voice that cannot be
    read never costs the text: the rendering is kept as text with a reason."""
    reason = result.degraded.reason.value if result.degraded.active else None
    if reason == "none":
        reason = None
    if reason in _NO_AUDIO_REASONS:
        return None, reason
    audio_id = _ingest_rendering_audio(session, message, result)
    if audio_id is None:
        return None, reason or "tts_skipped"
    return audio_id, reason


def _ingest_rendering_audio(
    session: Session, message: Message, result: RenderResult
) -> uuid.UUID | None:
    """Read the render service's output file (by NAME, from
    AI_RENDER_AUDIO_ROOT -- the service reports a path on its own disk) and
    store it as a media object owned by the message's author."""
    settings = get_settings()
    root = settings.AI_RENDER_AUDIO_ROOT
    uri = result.audio.uri
    if not root or not uri:
        return None
    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        return None
    name = PurePosixPath(unquote(parsed.path)).name
    if not name:
        return None
    path = Path(root) / name
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
        if size == 0 or size > settings.MEDIA_MAX_UPLOAD_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None

    sha256_hex = hashlib.sha256(data).hexdigest()
    existing = find_media_object(session, author_id=message.author_id, sha256_hex=sha256_hex)
    if existing is not None:
        return existing.id
    media_id = uuid.uuid4()
    storage = get_media_storage()
    storage.put(str(media_id), data)
    try:
        row, created = create_media_object(
            session,
            media_id=media_id,
            author_id=message.author_id,
            format=result.audio.format.value,
            sha256_hex=sha256_hex,
            size_bytes=len(data),
            duration_ms=result.audio.duration_ms,
        )
    except Exception:
        storage.delete(str(media_id))
        raise
    if not created:
        storage.delete(str(media_id))
    return row.id


def _translate_notice(client: AiClient, decision: ModerationDecision, author: User) -> str | None:
    """The classifier's notice is an English master (the mock's is Telugu).
    Returns it in the sender's language, or None -- recorded honestly as 'no
    notice produced' -- rather than the untranslated text passed off as what
    the sender was told."""
    target = _primary_subtag(author.preferred_language)
    if target not in SUPPORTED_LANGUAGES:
        return None
    if decision.nudge_language is not None and decision.nudge_language.value == target:
        return decision.nudge_text
    try:
        rendered = client.render(
            RenderRequest(pivot_text=decision.nudge_text, target_languages=[LanguageCode(target)])
        )
    except AiCallError as exc:
        logger.warning("could not translate the moderation notice: %s", exc)
        return None
    for result in rendered.results:
        if result.language.value == target and result.text.strip():
            return result.text.strip()
    return None


# --- finishing -----------------------------------------------------------------------------


def _holds(action: str) -> bool:
    if action == ModerationAction.NUDGE.value:
        return not get_settings().PIPELINE_NUDGE_DELIVERS
    return action in (ModerationAction.HOLD.value, ModerationAction.BLOCK.value)


def _finish(session: Session, message: Message, action: str, *, state: str) -> None:
    """Apply the verdict, record completion, and only THEN read the clock
    (ORCHESTRATOR_DESIGN.md #2): `state` must be committed before deciding
    whether to deliver, so a fan-out that skipped while the pipeline was
    pending is always followed by this delivery."""
    message_id = message.id
    new_status = None
    if _holds(action):
        target = "blocked" if action == ModerationAction.BLOCK.value else "held"
        # False when an earlier (crashed) attempt already applied it.
        if set_message_status(session, message_id, new_status=target, expected="pending"):
            new_status = target
    session.execute(
        update(Message)
        .where(Message.id == message_id, Message.pipeline_state == "pending")
        .values(pipeline_state=state)
    )
    session.commit()
    session.refresh(message)

    if new_status is not None:
        _notify_author(message_id, new_status)
        return
    if _holds(action) or message.status != "pending":
        return
    expires = message.undo_expires_at
    if expires is None or expires <= datetime.now(UTC):
        _request_delivery(message_id)


def _hold_for_pipeline(session: Session, message: Message, reason: str, state: str) -> None:
    """Fail closed: a message the pipeline could not rule on is held for a
    person, with a SYSTEM event saying why."""
    record_moderation_event(
        session,
        message_id=message.id,
        actor_kind="system",
        label=_UNCLASSIFIED_LABEL,
        action="HOLD",
        rationale=reason,
        policy_version=_SYSTEM_POLICY_VERSION,
    )
    session.commit()
    _finish(session, message, ModerationAction.HOLD.value, state=state)


def handle_pipeline_dead(session: Session, payload: dict, error: str) -> None:
    """The job exhausted its retries or failed permanently.

    - Before any verdict: the message is HELD (we cannot verify it, so we do
      not send it), with a SYSTEM event carrying the reason.
    - After a verdict (e.g. only the render stage kept failing): finish the
      pipeline anyway -- translations are an enhancement, moderation is the
      gate -- so an approved message is not stranded behind a dead render
      service.
    - A message that is no longer pending (already out, undone): leave it,
      just record that the pipeline failed."""
    message_id = uuid.UUID(payload["message_id"])
    message = session.get(Message, message_id)
    if message is None:
        return
    if message.status not in ("pending", "held", "blocked"):
        _mark_failed(session, message_id)
        return
    event = latest_moderation_event(session, message_id)
    if event is not None and event.actor_kind == "classifier":
        _finish(session, message, event.action, state="complete")
        return
    _hold_for_pipeline(session, message, f"Pipeline failed: {error}", "failed")


def _mark_failed(session: Session, message_id: uuid.UUID) -> None:
    session.execute(
        update(Message)
        .where(Message.id == message_id, Message.pipeline_state == "pending")
        .values(pipeline_state="failed")
    )
    session.commit()
