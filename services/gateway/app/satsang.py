"""The scheduled satsang broadcast (`/sessions*`, `contracts/chat/satsang.py`).

A moderator or admin schedules a satsang and names its speaker; the speaker's phone pushes audio to
an RTMP server (nginx-rtmp); the RTMP server asks THIS module, through two hooks, whether to let
it in and when it stopped. Elders see a list of what is coming and what is on.

Who may do what:

- Schedule, cancel, end, list everything (`/sessions/manage`): a moderator or admin, by the
  DATABASE role (`users.role`, read per request, as app/tokens.py explains) and only with a SIGNED
  token. A bare-UUID token is refused (401) for these, as for /admin and /moderation: in the
  default `legacy` mode a bare UUID IS that user, so anyone who knew a moderator's id could
  otherwise decide who broadcasts to the community.
- Get a fresh stream key (`POST /sessions/{id}/key`): the satsang's speaker, or a moderator or
  admin (that is how a leaked key is dealt with), with a signed token. Anyone else is told the
  session does not exist (404), the same answer as for a session that is not there -- exactly as
  app/media.py answers for held audio, because a 403 would confirm that a guessable id is real.
- List what is coming (`GET /sessions`): any signed-in user, legacy token included (that is what
  the elder app sends on staging today). It returns a name for the speaker, never an id, and never
  a key.

The stream key is a credential and nothing here can read one back: only its SHA-256 is stored
(app/db/satsang.py), so it exists in exactly two responses, create and rotate. A speaker who
loses theirs asks for a new one; they cannot be shown the old one, because the gateway does not
have it. Two sessions cannot share a key (a unique index); if the generator ever repeats itself,
creating retries rather than failing, and gives up with a 503 rather than a 500.

Status moves are checked twice: here (a 409), and by a trigger in the database that refuses any
move other than scheduled->live, scheduled->cancelled and live->ended, so no route written later
can put a finished satsang back on the air.

THE INGEST HOOKS -- the one genuinely adversarial surface
=========================================================

`POST /sessions/ingest/publish` (nginx-rtmp's `on_publish`) and `POST /sessions/ingest/done`
(`on_publish_done`) are called by the media server, not by a browser, and their answer decides
whether a stranger can broadcast into a devotional space. No user token authorises them; not even
an admin's opens them (a test pins that). What stands between an attacker who can reach the
gateway and a successful call, in order, and what each layer is actually worth:

1. **Reachability** (configuration, not code). The gateway publishes no host port; the only way in
   from outside is Caddy, and Caddy must never route `/sessions/ingest*`. That refusal is added
   with the Caddy route in the infrastructure phase, above the `/sessions` route. Nothing in this
   module can enforce it, which makes it the layer most likely to be broken by a later edit.
2. **A shared secret** (`SATSANG_HOOK_SECRET`), in the callback URL's query string
   (`...?secret=`), compared in constant time. nginx-rtmp cannot add a header to its callback, so
   the URL is the only channel it offers. Unset, every hook answers 503 (fail closed: nobody goes
   live on an unconfigured gateway). Wrong, empty or missing, 403, before the body is read and
   before the database is touched.
3. **The stream key itself.** A call must carry the key of a satsang that is scheduled (or already
   live): 256 random bits, looked up by their SHA-256, so there is nothing to guess and no timing
   difference to measure. An attacker who has the secret but no key changes nothing.
4. **What a key is worth is bounded.** It works only while its satsang is scheduled or live, only
   from an hour before the start, and it dies when that satsang ends. One satsang can be live at
   a time (a unique index).

Is that enough? For the HOOK, yes, for what it is. But the hook is not the lock. A holder of a
valid key can already broadcast by publishing to the RTMP port with it; the hook is only the gate
the RTMP server consults. So what has to be protected is the key, and its weakness is not here: **RTMP
carries the key in clear**, and the ingest port is on the campus network (infra/satsang/
INGEST_FINDING.md). Anyone who can watch one publish handshake -- on the same Wi-Fi, say -- learns
a key that works until that satsang ends. Bounding the key's life (point 4) shortens that; it does
not close it. Recorded as OPEN_QUESTIONS #36, with two smaller things true of the secret: it is
in the query string, so it appears in the gateway's own access log (whoever can read that log
holds it), and it is one value shared by both hooks.

Not verified, and to be verified when the media server exists: that nginx-rtmp keeps the query
string of its configured callback URL, and the exact form fields it posts. This module reads the
stream name from a form field called `name` (what nginx-rtmp posts) or from JSON `stream_key`
(what the mock documents), and nothing else.

Known limit, not decided here: when a speaker's connection drops, `on_publish_done` ends the
satsang, and the key is then dead, so the speaker cannot resume. The same key publishing again
while the satsang is still live is accepted (a reconnect that arrives before the old connection's
done-hook), but after the done-hook it is not. A grace period belongs to the watchdog that also
has to catch the opposite failure, a satsang left live because the done-hook never came. Until
then a moderator can end one by hand (`POST /sessions/{id}/end`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

from contracts.chat.satsang import (
    STREAM_KEY_BYTES,
    SatsangSessionAdminList,
    SatsangSessionCreate,
    SatsangSessionCreated,
    SatsangSessionList,
    SatsangSessionOut,
    SatsangSessionPublic,
    SatsangStatus,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import bearer, get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import User as DbUser
from app.db.satsang import SatsangSession
from app.models import User
from app.tokens import looks_like_jwt

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["satsang"])

_PRIVILEGED = ("moderator", "admin")
# A start a few minutes in the past is a clock difference, not a mistake; an hour ago is.
_START_GRACE = timedelta(minutes=5)
# An elder is not shown a no-show, or a cancellation, from days ago.
_LISTING_WINDOW = timedelta(hours=24)
_LISTING_LIMIT = 50
_CONSOLE_LIMIT = 200
# nginx-rtmp posts a few hundred bytes. Anything larger is not it.
_MAX_HOOK_BODY = 8 * 1024
_MAX_KEY_LENGTH = 256
_KEY_ATTEMPTS = 5
_UQ_KEY = "uq_sessions_stream_key_sha256"
_UQ_ONE_LIVE = "uq_sessions_one_live"
_NOT_FOUND = HTTPException(status_code=404, detail="Session not found")


def _new_stream_key() -> str:
    return secrets.token_urlsafe(STREAM_KEY_BYTES)


def hash_stream_key(key: str) -> str:
    """What is stored: lowercase hex SHA-256 of the key's UTF-8 bytes (the table's CHECK)."""
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


# --- who is calling -------------------------------------------------------------------------------


def _signed_token_only(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    """Runs BEFORE get_current_user (it is declared first wherever it is used), so a refused legacy
    token never reaches the stub that would provision a users row for it. No header at all is left
    to get_current_user, which answers 401 "Not authenticated"."""
    if creds is not None and not looks_like_jwt(creds.credentials):
        raise HTTPException(status_code=401, detail="This route requires a signed token")


def _signed_user(
    _signed: None = Depends(_signed_token_only),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DbUser:
    try:
        row = db.get(DbUser, uuid.UUID(user.id))
    except ValueError:  # a non-UUID stub token has no row
        row = None
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return row


def require_moderator(caller: DbUser = Depends(_signed_user)) -> DbUser:
    if caller.role not in _PRIVILEGED:
        raise HTTPException(status_code=403, detail="Insufficient role")
    return caller


# --- mapping --------------------------------------------------------------------------------------


def _names(db: Session, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = db.execute(select(DbUser.id, DbUser.name).where(DbUser.id.in_(user_ids))).all()
    return dict(rows)


def _admin_view(row: SatsangSession, names: dict[uuid.UUID, str]) -> SatsangSessionOut:
    return SatsangSessionOut(
        id=str(row.id),
        title=row.title,
        speaker_user_id=str(row.speaker_user_id),
        speaker_name=names.get(row.speaker_user_id, ""),
        scheduled_start_at=row.scheduled_start_at,
        status=SatsangStatus(row.status),
        started_at=row.started_at,
        ended_at=row.ended_at,
        reminder_sent_at=row.reminder_sent_at,
        created_by=str(row.created_by),
        created_at=row.created_at,
    )


def _public_view(row: SatsangSession, names: dict[uuid.UUID, str]) -> SatsangSessionPublic:
    live = row.status == "live"
    hls = f"{get_settings().SATSANG_HLS_PATH.rstrip('/')}/{row.id}.m3u8" if live else None
    return SatsangSessionPublic(
        id=str(row.id),
        title=row.title,
        speaker_name=names.get(row.speaker_user_id, ""),
        scheduled_start_at=row.scheduled_start_at,
        status=SatsangStatus(row.status),
        hls_url=hls,
    )


def _created(db: Session, row: SatsangSession, key: str) -> SatsangSessionCreated:
    base = get_settings().SATSANG_INGEST_BASE_URL.rstrip("/")
    names = _names(db, {row.speaker_user_id})
    return SatsangSessionCreated(
        session=_admin_view(row, names), stream_key=key, ingest_url=f"{base}/{key}"
    )


def _violated(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def _row_or_none(db: Session, session_id: str, *, lock: bool = False) -> SatsangSession | None:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    stmt = select(SatsangSession).where(SatsangSession.id == sid)
    return db.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()


# --- scheduling -----------------------------------------------------------------------------------


@router.post("", response_model=SatsangSessionCreated, status_code=201)
def schedule_session(
    body: SatsangSessionCreate,
    caller: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> SatsangSessionCreated:
    try:
        speaker = db.get(DbUser, uuid.UUID(body.speaker_user_id))
    except ValueError:
        speaker = None
    if speaker is None:
        raise HTTPException(status_code=404, detail="Speaker not found")
    if body.scheduled_start_at < _now() - _START_GRACE:
        raise HTTPException(status_code=422, detail="scheduled_start_at is in the past")

    for _ in range(_KEY_ATTEMPTS):
        key = _new_stream_key()
        row = SatsangSession(
            title=body.title,
            speaker_user_id=speaker.id,
            scheduled_start_at=body.scheduled_start_at,
            stream_key_sha256=hash_stream_key(key),
            created_by=caller.id,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            if _violated(exc) != _UQ_KEY:
                raise
            continue  # the generator repeated itself: draw again
        db.commit()
        logger.info("satsang %s scheduled by %s", row.id, caller.id)
        return _created(db, row, key)
    raise HTTPException(status_code=503, detail="Could not make a unique stream key")


@router.get("", response_model=SatsangSessionList)
def list_sessions(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SatsangSessionList:
    """What an elder sees: what is on now, what is coming, and what was called off. Not what has
    ended (the archive is a separate feature), and not a no-show or a cancellation from days ago."""
    cutoff = _now() - _LISTING_WINDOW
    stmt = (
        select(SatsangSession)
        .where(
            or_(
                SatsangSession.status == "live",
                and_(
                    SatsangSession.status.in_(("scheduled", "cancelled")),
                    SatsangSession.scheduled_start_at >= cutoff,
                ),
            )
        )
        .order_by(SatsangSession.scheduled_start_at, SatsangSession.id)
        .limit(_LISTING_LIMIT)
    )
    rows = list(db.execute(stmt).scalars())
    names = _names(db, {r.speaker_user_id for r in rows})
    return SatsangSessionList(items=[_public_view(r, names) for r in rows])


@router.get("/manage", response_model=SatsangSessionAdminList)
def list_sessions_for_console(
    _caller: DbUser = Depends(require_moderator), db: Session = Depends(get_db)
) -> SatsangSessionAdminList:
    """Every status, newest first -- and still no stream key, because none is stored."""
    stmt = (
        select(SatsangSession)
        .order_by(SatsangSession.created_at.desc(), SatsangSession.id.desc())
        .limit(_CONSOLE_LIMIT)
    )
    rows = list(db.execute(stmt).scalars())
    names = _names(db, {r.speaker_user_id for r in rows})
    return SatsangSessionAdminList(items=[_admin_view(r, names) for r in rows])


# --- status moves a person makes ------------------------------------------------------------------


@router.post("/{session_id}/cancel", response_model=SatsangSessionOut)
def cancel_session(
    session_id: str,
    _caller: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> SatsangSessionOut:
    """Only from `scheduled`. A satsang that is on the air is ended, not cancelled."""
    row = _row_or_none(db, session_id, lock=True)
    if row is None:
        raise _NOT_FOUND
    if row.status != "scheduled":
        raise HTTPException(status_code=409, detail=f"a {row.status} satsang cannot be cancelled")
    row.status = "cancelled"
    db.commit()
    return _admin_view(row, _names(db, {row.speaker_user_id}))


@router.post("/{session_id}/end", response_model=SatsangSessionOut)
def end_session(
    session_id: str,
    _caller: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> SatsangSessionOut:
    """A moderator ends a live satsang by hand: the speaker's phone died and the end-of-stream
    hook never came, or someone has to be cut off. Only from `live`; a satsang that never went on
    the air is cancelled, not ended."""
    row = _row_or_none(db, session_id, lock=True)
    if row is None:
        raise _NOT_FOUND
    if row.status != "live":
        raise HTTPException(status_code=409, detail=f"a {row.status} satsang cannot be ended")
    row.status = "ended"
    row.ended_at = _now()
    db.commit()
    logger.info("satsang %s ended by a moderator", row.id)
    return _admin_view(row, _names(db, {row.speaker_user_id}))


@router.post("/{session_id}/key", response_model=SatsangSessionCreated)
def rotate_stream_key(
    session_id: str,
    caller: DbUser = Depends(_signed_user),
    db: Session = Depends(get_db),
) -> SatsangSessionCreated:
    """A new key, shown once; the old one stops working at once. Open to the satsang's speaker and
    to any moderator or admin, and to nobody else: anyone else gets the 404 a missing session gets,
    body and all."""
    row = _row_or_none(db, session_id, lock=True)
    allowed = row is not None and (row.speaker_user_id == caller.id or caller.role in _PRIVILEGED)
    if not allowed:
        raise _NOT_FOUND
    if row.status != "scheduled":
        # Live: rotating would orphan the running stream, whose end-of-stream hook would then carry
        # a key nobody recognises, leaving the satsang live for ever. Ended/cancelled: nothing to
        # broadcast.
        raise HTTPException(status_code=409, detail=f"a {row.status} satsang cannot be re-keyed")
    for _ in range(_KEY_ATTEMPTS):
        key = _new_stream_key()
        try:
            with db.begin_nested():
                row.stream_key_sha256 = hash_stream_key(key)
                db.flush()
        except IntegrityError as exc:
            if _violated(exc) != _UQ_KEY:
                raise
            continue
        db.commit()
        logger.info("satsang %s re-keyed by %s", row.id, caller.id)
        return _created(db, row, key)
    raise HTTPException(status_code=503, detail="Could not make a unique stream key")


# --- the ingest hooks: called by the RTMP server --------------------------------------------------


def _check_hook_secret(request: Request) -> None:
    """Fail closed. See the module docstring for what this is, and is not, worth."""
    expected = get_settings().SATSANG_HOOK_SECRET
    if not expected:
        raise HTTPException(status_code=503, detail="Ingest hooks are not configured")
    provided = request.query_params.get("secret", "")
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        logger.warning("satsang hook refused: wrong or missing secret")
        raise HTTPException(status_code=403, detail="Forbidden")


async def _read_stream_key(request: Request) -> str | None:
    """The stream name from the body, reading at most _MAX_HOOK_BODY bytes. nginx-rtmp posts
    `application/x-www-form-urlencoded` with the name in `name`; the mock documents JSON with
    `stream_key`. Anything unreadable is simply "no key"."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_HOOK_BODY:
        raise HTTPException(status_code=413, detail="Body too large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _MAX_HOOK_BODY:
            raise HTTPException(status_code=413, detail="Body too large")
    if not body:
        return None
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            data = json.loads(body)
            raw = data.get("name") or data.get("stream_key") if isinstance(data, dict) else None
        else:
            fields = parse_qs(body.decode("utf-8", errors="replace"))
            raw = (fields.get("name") or fields.get("stream_key") or [None])[0]
    except ValueError:
        return None
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    return key if key and len(key) <= _MAX_KEY_LENGTH else None


def _by_key(db: Session, key: str | None) -> SatsangSession | None:
    if not key:
        return None
    stmt = select(SatsangSession).where(SatsangSession.stream_key_sha256 == hash_stream_key(key))
    return db.execute(stmt.with_for_update()).scalar_one_or_none()


def _refuse(why: str) -> HTTPException:
    # The reason goes to the log; the caller is only ever told "Forbidden", and no key is logged.
    logger.warning("satsang publish refused: %s", why)
    return HTTPException(status_code=403, detail="Forbidden")


def _on_publish(db: Session, key: str | None) -> JSONResponse:
    row = _by_key(db, key)
    if row is None:
        raise _refuse("unknown or empty key")
    if row.status == "live":
        # A phone that dropped and reconnected asks again before the old connection's done-hook
        # has fired. Same speaker, same key: let it through and change nothing.
        return JSONResponse({"status": "live"})
    if row.status != "scheduled":
        raise _refuse(f"satsang {row.id} is {row.status}")
    early = timedelta(minutes=get_settings().SATSANG_GO_LIVE_EARLY_MINUTES)
    if _now() < row.scheduled_start_at - early:
        raise _refuse(f"satsang {row.id} is too early")
    try:
        with db.begin_nested():
            row.status = "live"
            row.started_at = _now()
            db.flush()
    except IntegrityError as exc:
        if _violated(exc) != _UQ_ONE_LIVE:
            raise
        raise _refuse(f"satsang {row.id}: another satsang is already live") from exc
    db.commit()
    logger.info("satsang %s is live", row.id)
    return JSONResponse({"status": "live"})


def _on_done(db: Session, key: str | None) -> JSONResponse:
    row = _by_key(db, key)
    if row is None:
        raise _refuse("end-of-stream for an unknown key")
    if row.status != "live":
        raise HTTPException(status_code=409, detail=f"this satsang is {row.status}")
    row.status = "ended"
    row.ended_at = _now()
    db.commit()
    logger.info("satsang %s ended (publisher stopped)", row.id)
    return JSONResponse({"status": "ended"})


@router.post("/ingest/publish")
async def on_publish(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """2xx lets the publisher in (and puts a scheduled satsang on the air); anything else drops it."""
    _check_hook_secret(request)
    key = await _read_stream_key(request)
    return await run_in_threadpool(_on_publish, db, key)


@router.post("/ingest/done")
async def on_publish_done(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """The publisher stopped: the satsang has ended."""
    _check_hook_secret(request)
    key = await _read_stream_key(request)
    return await run_in_threadpool(_on_done, db, key)
