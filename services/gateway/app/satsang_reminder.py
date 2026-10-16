"""The satsang reminder: a push, thirty minutes before a satsang starts, in each member's language.

It rides the existing job queue, so nothing here is a clock or a sweep loop. Scheduling a satsang
enqueues ONE `satsang_reminder` job with `jobs.next_attempt_at = scheduled_start_at - 30 min`;
`claim_next_job` (app/db/repository.py) claims a queued job only when that time has passed, and the
queue already supplies retries with backoff, dead-lettering after JOB_MAX_ATTEMPTS, and reclaiming a
job whose worker died. This module is the handler and the one place a job is made.

**Who gets it (decided in contracts/chat/satsang.py and OPEN_QUESTIONS #44).** A satsang is not scoped
to a circle; it is for the whole community (proposal 7.4, objective O4). So the audience is every user
with a push subscription, and the same answer decides who sees the listing.

**One body, rendered once per language.** The text is written once, in English, then rendered ONCE per
distinct language the recipients read: a single render call for K languages (the render service
translates and speaks), never one per member and never one per language. The satsang's title is NOT
machine-translated: it travels as the push title untouched, and the translated sentence avoids the
word "satsang" (the Week 8 comparison measured the translator turning it into "chess" in Hindi). Each
language's text and audio are stored in `satsang_reminder_renderings`, the same shape as a message's
renderings, and read back by `GET /sessions/{id}/reminder` and `.../reminder/audio` (gateway-local
shapes, like app/push.py's: they are promoted to contracts/chat/ when a client reads them).

**A reminder nobody receives is worse than one in the wrong language.** If the render service is down
the reminder goes out in the English source text, marked `translated = False, text_only`, and is not
retried; if only speech is down it is translated text without audio (`text_only` / `tts_skipped`, the
reasons message renderings already use); a member who turned speech off in their settings gets the
text and no audio, which is a choice, not a failure.

**Transactions.** The render service takes seconds, and Postgres kills a connection left idle in a
transaction for 30 s (app/db/base.py). So the handler decides, ENDS its transaction, makes the call
with none open, then locks the satsang row again and re-checks everything before it delivers: what was
true before a slow call is not trusted after it (a satsang can be cancelled meanwhile). The same rule
holds for the pushes (OPEN_QUESTIONS #49, measured): they are made with NO lock and NO transaction
held. `deliver` saves the renderings and COMMITS, which ends the lock; then each recipient is served on
their own (see **Sent once**). Two workers racing are kept apart by the per-recipient claim, not by a
lock held across the network.

**Quiet hours: send anyway, unless the user chose a window themselves.** app/push.py resolves a user's
"never picked a window" (NULL) to a default of 20:00-07:00, and a reminder is useless an hour late, so
that default must not apply: it would silently drop every early-morning satsang's reminder (04:30 for a
05:00 start). But a window the user EXPLICITLY set is their stated wish, so it is honoured, and "the
elder misses it" is then their choice. (The alternative, refusing to schedule a satsang whose reminder
would fall in quiet hours, cannot work: windows are per user and we cannot know them at creation.) To
change this, change `_silenced`.

**A satsang that is moved after its reminder was enqueued (design (b)).** The handler does not trust what
was true when the job was made: it re-reads the satsang's CURRENT `scheduled_start_at`. If the start was
moved later and the job fires too early, it enqueues itself again for the new time and sends nothing; at
the right time, it sends. I chose this over (a), re-enqueue on edit and cancel the old job, because the
satsang row is then the single source of truth: there is no old job to find, nothing to cancel, so no
orphan that could still fire at the old time (worse than no job). The costs, stated: one wake-up when a
satsang is moved later; and a satsang moved EARLIER than its old reminder time is reminded at the OLD
time (late), or not at all if it has started by then (`test_KNOWN_LIMIT_...`). There is no reschedule
route yet; the one that adds it should also call `enqueue_reminder`, which is safe because the handler
is idempotent.

**Sent once, and what a crash costs.** Each recipient has a row in `satsang_reminder_deliveries`:
CLAIMED (committed) before their push, COMPLETED after it. A claim that is never completed belongs to a
worker that died, and a run that finds it older than `CLAIM_LEASE` takes it over. So: a process that
dies mid-push costs ONE duplicate (the user in flight), never a missing reminder and never a resend to
everybody; two workers (a duplicate job) never push the same user twice, because the second finds the
first's claim and moves on, leaving that user to a retry (`ReminderNotFinished`) if the first never
finishes. `reminder_sent_at`, a timestamp not a flag, is stamped last, in the transaction the worker
commits together with the job's completion (app/jobs.py), only when everybody is served; a second run
sees it and sends nothing. The choice of at-least-once over at-most-once is deliberate: the reminder is
how an elder who is not looking at the app learns of the discourse, so a duplicate "starts in 30
minutes" is the cheaper mistake than a missing one, and the ledger keeps that mistake to one person
per crash. (The code before this change was also at-least-once, but with the whole audience as the
duplicate: a crash after some pushes rolled the batch back and the retry pushed all of them again.)
`send_push` has a timeout (`PUSH_TIMEOUT_S`): without one a push service that accepts the connection
and never answers wedged the worker for good, measured.

**What is NOT here.** English readers get the text and NO audio: the real render service has no
English voice (measured on 14 Oct: asked for `en` it answers `text_only`, "no TTS voice for this
language"), which this module records as it is rather than hiding. The Telugu and Hindi voices are
Piper's, and nobody who reads those languages has listened to them. There is no per-user opt-out of
satsang reminders beyond turning push off.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from contracts.ai.language import LanguageCode
from contracts.ai.render import RenderRequest, RenderResult
from pydantic import BaseModel
from sqlalchemy import and_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ai_client import AiCallError, AiClient
from app.config import get_settings
from app.db.models import MediaObject, PushSubscription
from app.db.models import User as DbUser
from app.db.repository import enqueue_job
from app.db.satsang import SatsangReminderDelivery, SatsangReminderRendering, SatsangSession
from app.jobs import PermanentJobError
from app.media_storage import get_media_storage
from app.pipeline import _rendering_audio
from app.push import is_quiet_hours, send_push

logger = logging.getLogger(__name__)

JOB_TYPE = "satsang_reminder"
LEAD = timedelta(minutes=30)
SOURCE_LANGUAGE = "en"
# How long a claim on one recipient may stay un-completed before another run takes it over. It must
# exceed the longest a single recipient's pushes can take (a few devices, each bounded by
# app/push.py's PUSH_TIMEOUT_S) and stay under the job's own lease (JOB_LEASE_SECONDS, 300), so that by
# the time a crashed job is retried, its claim has expired.
CLAIM_LEASE = timedelta(minutes=2)


class ReminderNotFinished(Exception):
    """Somebody is still unserved: another worker holds their claim, or their push blew up. Raised so the
    queue retries later with its backoff; recipients already served are never pushed again."""


_AUDIO_TYPES = {"wav_pcm16": "audio/wav", "ogg_opus": "audio/ogg", "mp3": "audio/mpeg"}


def _now() -> datetime:
    return datetime.now(UTC)


def _ai_client() -> AiClient:
    return AiClient.from_settings(get_settings())


def reminder_due_at(scheduled_start_at: datetime) -> datetime:
    return scheduled_start_at - LEAD


def source_text(minutes_left: int) -> str:
    """The one body, in the source language. No title (it is not machine-translated) and not the word
    "satsang" (see the module docstring)."""
    unit = "minute" if minutes_left == 1 else "minutes"
    return f"A discourse begins in {minutes_left} {unit}."


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


# --- who ---------------------------------------------------------------------------------------------


def _language_of(user: DbUser) -> str:
    return (user.preferred_language or SOURCE_LANGUAGE).split("-")[0].lower()


def _silenced(user: DbUser, now: datetime) -> bool:
    """Quiet hours apply only to a window the user chose themselves; the app's default does not (see
    the module docstring)."""
    if user.quiet_hours_start is None and user.quiet_hours_end is None:
        return False
    return is_quiet_hours(
        timezone=user.timezone,
        quiet_hours_start=user.quiet_hours_start,
        quiet_hours_end=user.quiet_hours_end,
        now=now,
    )


def _recipients(session: Session, now: datetime) -> list[DbUser]:
    """Every user with a push subscription (once, however many devices), minus anyone inside a quiet
    window they set themselves."""
    user_ids = sorted(session.scalars(select(PushSubscription.user_id).distinct()), key=str)
    users = (session.get(DbUser, user_id) for user_id in user_ids)
    return [user for user in users if user is not None and not _silenced(user, now)]


def _languages_to_render(recipients: list[DbUser]) -> list[str]:
    renderable = {language.value for language in LanguageCode}
    return sorted({_language_of(user) for user in recipients} & renderable)


# --- rendering ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rendered:
    """One language the render service returned usable text for."""

    language: str
    text: str
    result: RenderResult


def render_for_languages(text: str, languages: list[str]) -> dict[str, Rendered]:
    """One render call for every language, with no database involved. Whatever it cannot give is
    simply absent from the answer, and the caller falls back to the source text: a failure here is
    never allowed to stop the reminder."""
    if not languages:
        return {}
    client = _ai_client()
    try:
        response = client.render(
            RenderRequest(
                pivot_text=text, target_languages=[LanguageCode(code) for code in languages]
            )
        )
    except AiCallError as exc:
        logger.warning("satsang reminder: render unavailable, sending the source text: %s", exc)
        return {}
    finally:
        client.close()
    rendered: dict[str, Rendered] = {}
    for result in response.results:
        language, body = result.language.value, result.text.strip()
        if language in languages and body:
            rendered[language] = Rendered(language, body, result)
    return rendered


# --- the handler -------------------------------------------------------------------------------------


def _locked(session: Session, session_id: uuid.UUID) -> SatsangSession | None:
    return session.execute(
        select(SatsangSession).where(SatsangSession.id == session_id).with_for_update()
    ).scalar_one_or_none()


def _why_not(row: SatsangSession | None, now: datetime) -> str | None:
    """Why this satsang should not be reminded about right now (None: it should be). A later phase
    uses it again after the slow call, so the same rules decide both times."""
    if row is None:
        return "no such satsang"
    if row.status != "scheduled":
        return f"it is {row.status}"
    if row.reminder_sent_at is not None:
        return "already sent"
    if row.scheduled_start_at <= now:
        return "its start has passed"
    return None


def handle_satsang_reminder(session: Session, payload: dict) -> None:
    """Run by the job worker, which commits whatever this writes together with the job's completion
    and rolls it all back (and retries) if this raises. Every early return below is a COMPLETED job:
    there is nothing to retry."""
    try:
        session_id = uuid.UUID(str(payload["session_id"]))
    except (KeyError, ValueError) as exc:
        raise PermanentJobError(f"a satsang_reminder job needs a session_id: {payload!r}") from exc

    now = _now()
    row = _locked(session, session_id)
    why = _why_not(row, now)
    if why is not None:
        logger.info("satsang reminder %s: %s, not reminding", session_id, why)
        return
    start = row.scheduled_start_at
    if now < reminder_due_at(start):
        # Moved later since this job was made: ask again for the new time, send nothing now.
        enqueue_reminder(session, row)
        logger.info("satsang reminder %s: moved later, re-enqueued for its new time", row.id)
        return

    minutes_left = max(1, math.ceil((start - now).total_seconds() / 60))
    text = source_text(minutes_left)
    languages = _languages_to_render(_recipients(session, now))
    rendered: dict[str, Rendered] = {}
    if languages:
        session.commit()  # end the transaction: nothing is held open across the slow call
        rendered = render_for_languages(text, languages)
        now = _now()
        row = _locked(session, session_id)
        why = _why_not(row, now)
        if why is not None:
            logger.info("satsang reminder %s: %s after the render, not reminding", session_id, why)
            return

    pending = deliver(session, row, minutes_left, rendered) or []
    if pending:
        raise ReminderNotFinished(
            f"satsang reminder {session_id}: {len(pending)} recipient(s) not served yet"
        )
    _finish(session, session_id)
    logger.info("satsang reminder %s: sent", session_id)


def _store(
    session: Session,
    row: SatsangSession,
    *,
    language: str,
    text: str,
    translated: bool,
    rendered: Rendered | None = None,
    degraded_reason: str | None = None,
) -> SatsangReminderRendering:
    audio_id = None
    versions: dict[str, str | None] = {"translate": None, "tts": None}
    if rendered is not None:
        # The pipeline's own reader of the render service's output file: stored as a media object.
        author = SimpleNamespace(author_id=row.created_by)
        audio_id, degraded_reason = _rendering_audio(session, author, rendered.result)
        versions = {
            "translate": rendered.result.model_version_translate,
            "tts": rendered.result.model_version_tts,
        }
    return session.merge(
        SatsangReminderRendering(
            session_id=row.id,
            language=language,
            text=text,
            translated=translated,
            audio_media_object_id=audio_id,
            degraded_reason=degraded_reason,
            model_version_translate=versions["translate"],
            model_version_tts=versions["tts"],
        )
    )


def deliver(
    session: Session, row: SatsangSession, minutes_left: int, rendered: dict[str, Rendered]
) -> list[uuid.UUID]:
    """Store each language's reminder, then push every recipient the one in their own language. A seam:
    the clock's tests replace it.

    The renderings are saved and the transaction COMMITTED (which ends the lock on the satsang row)
    BEFORE the first push: a push is a network call, and nothing here may hold a lock or a transaction
    across one. Each recipient is then served on their own (`_send_all`). Returns the recipients who are
    not served yet (empty: everybody is), for the handler to retry."""
    now = _now()
    source = source_text(minutes_left)
    recipients = _recipients(session, now)
    stored: dict[str, SatsangReminderRendering] = {}
    for language, item in rendered.items():
        stored[language] = _store(
            session, row, language=language, text=item.text, translated=True, rendered=item
        )
    # The source text is always readable, for anyone whose language was not rendered.
    if SOURCE_LANGUAGE not in stored:
        stored[SOURCE_LANGUAGE] = _store(
            session, row, language=SOURCE_LANGUAGE, text=source, translated=True
        )
    # A language a recipient reads that could not be rendered: they get the source text, and it says so.
    renderable = {language.value for language in LanguageCode}
    for language in sorted({_language_of(user) for user in recipients}):
        if language not in stored:
            reason = "text_only" if language in renderable else None
            stored[language] = _store(
                session,
                row,
                language=language,
                text=source,
                translated=False,
                degraded_reason=reason,
            )
    session.flush()
    # Everything the sends need is read BEFORE the commit: afterwards nothing may be loaded lazily.
    session_id, title = row.id, row.title
    to_serve = [(user.id, stored[_language_of(user)].text) for user in recipients]
    session.commit()
    return _send_all(session, session_id, title, to_serve)


def _send_all(
    session: Session, session_id: uuid.UUID, title: str, to_serve: list[tuple[uuid.UUID, str]]
) -> list[uuid.UUID]:
    """One recipient at a time: claim, push, record. See `SatsangReminderDelivery` for why the order is
    what it is. send_push never raises for a bad device; if it does raise (a bug, a dead database
    connection) that recipient's claim is left un-completed, the others are still served, and the
    handler is told who is left."""
    pending: list[uuid.UUID] = []
    for user_id, body in to_serve:
        if not _still_wanted(session, session_id):
            return []  # cancelled, ended, or finished by another worker: nobody is left to tell
        state = _claim(session, session_id, user_id)
        if state == "done":
            continue
        if state == "busy":
            pending.append(user_id)
            continue
        try:
            send_push(session, user_id=user_id, title=title, body=body)
            _mark_sent(session, session_id, user_id)
        except Exception:
            logger.exception("satsang reminder %s: serving one recipient failed", session_id)
            session.rollback()
            pending.append(user_id)
    return pending


def _still_wanted(session: Session, session_id: uuid.UUID) -> bool:
    """A fresh, UNLOCKED read before each recipient: a satsang cancelled or ended halfway through the
    sends stops them, and so does another worker having finished."""
    row = session.get(SatsangSession, session_id, populate_existing=True)
    wanted = _why_not(row, _now()) is None
    if not wanted:
        session.rollback()
    return wanted


def _claim(session: Session, session_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Take this recipient, atomically. "mine": push them. "done": already pushed. "busy": another worker
    claimed them less than CLAIM_LEASE ago and has not finished (a claim older than that is a dead
    worker's, and is taken over here: the one case where a user can be pushed twice)."""
    now = _now()
    table = SatsangReminderDelivery
    won = session.execute(
        pg_insert(table)
        .values(session_id=session_id, user_id=user_id, claimed_at=now)
        .on_conflict_do_update(
            index_elements=[table.session_id, table.user_id],
            set_={"claimed_at": now},
            where=and_(table.sent_at.is_(None), table.claimed_at < now - CLAIM_LEASE),
        )
        .returning(table.user_id)
    ).first()
    if won is not None:
        state = "mine"
    else:
        sent_at = session.scalar(
            select(table.sent_at).where(table.session_id == session_id, table.user_id == user_id)
        )
        state = "done" if sent_at is not None else "busy"
    session.commit()  # the claim is saved BEFORE the push, so a death mid-push leaves it behind
    return state


def _mark_sent(session: Session, session_id: uuid.UUID, user_id: uuid.UUID) -> None:
    table = SatsangReminderDelivery
    session.execute(
        update(table)
        .where(table.session_id == session_id, table.user_id == user_id)
        .values(sent_at=_now())
    )
    session.commit()


def _finish(session: Session, session_id: uuid.UUID) -> None:
    """Everybody is served: stamp the satsang, in the transaction the worker commits together with the
    job's completion. Locks the row again, as the stamp must not race a second run's."""
    row = _locked(session, session_id)
    if row is not None and row.reminder_sent_at is None:
        row.reminder_sent_at = _now()
    session.flush()


# --- reading it back ---------------------------------------------------------------------------------


class SatsangReminderOut(BaseModel):
    """The reminder as one reader gets it. Gateway-local, like app/push.py's models: no client reads it
    yet, so it is not in contracts/chat/.

    `language` is the language `text` is really in; `translated` is False when that is the English
    source, either because the render service could not translate it or because nothing renders into
    this reader's language. `audio_url` is None when there is no audio or this reader turned speech off."""

    session_id: str
    language: str
    text: str
    translated: bool
    audio_url: str | None = None
    degraded_reason: str | None = None


def _reader_row(
    db: Session, session_id: uuid.UUID, user: DbUser
) -> tuple[SatsangReminderRendering, bool] | None:
    """(the row this reader gets, whether its text is in the reader's own language). A reader whose
    language was not rendered at all (nobody reading it was subscribed) gets the English source row,
    and is told it is not in their language."""
    rows = {
        r.language: r
        for r in db.scalars(
            select(SatsangReminderRendering).where(
                SatsangReminderRendering.session_id == session_id
            )
        )
    }
    own = rows.get(_language_of(user))
    if own is not None:
        return own, own.translated
    source = rows.get(SOURCE_LANGUAGE)
    return (source, False) if source is not None else None


def read_reminder(db: Session, session_id: str, user: DbUser) -> SatsangReminderOut | None:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    found = _reader_row(db, sid, user)
    if found is None:
        return None
    rendering, in_their_language = found
    audio = bool(rendering.audio_media_object_id) and user.tts_on is not False
    return SatsangReminderOut(
        session_id=str(sid),
        language=rendering.language if in_their_language else SOURCE_LANGUAGE,
        text=rendering.text,
        translated=in_their_language,
        audio_url=f"/sessions/{sid}/reminder/audio" if audio else None,
        degraded_reason=rendering.degraded_reason,
    )


def read_reminder_audio(db: Session, session_id: str, user: DbUser) -> tuple[bytes, str] | None:
    """(bytes, content type) of this reader's reminder audio, or None: none was made, the reader turned
    speech off, or the recording has been swept by retention."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    found = _reader_row(db, sid, user)
    if found is None or found[0].audio_media_object_id is None or user.tts_on is False:
        return None
    rendering = found[0]
    media = db.get(MediaObject, rendering.audio_media_object_id)
    if media is None:
        return None
    data = get_media_storage().get(str(media.id))
    if data is None:
        return None
    return data, _AUDIO_TYPES.get(media.format, "application/octet-stream")
