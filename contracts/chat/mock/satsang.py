"""
`/sessions*` in the mock chat gateway: what the elder app's satsang screens build against
before the real routes exist.

Same rules as the real gateway, so a client written against this works there: only a moderator
or admin schedules, cancels and ends; the speaker or a moderator may re-key, and anyone else is
told the session does not exist; **at most one session is live at a time**; a
session may go live from 60 minutes before its start and at any time after (a late speaker is
still giving that satsang); and an elder never receives the stream key, the speaker's internal
id, or our bookkeeping.

Two things are faithful to the real server rather than convenient:

- **Going live is not a user action.** `POST /sessions/ingest/publish` is the hook the RTMP
  server itself calls (nginx-rtmp's `on_publish`), carrying the stream NAME (the session id) and
  the key as a separate argument; `.../ingest/done` is `on_publish_done`. The stream is named by
  the session, not the key, because nginx-rtmp names the HLS files after the stream name.
  The key is what authorises going on air, which is why it is a
  credential. An app developer who needs a live session drives these two.
- **The key is returned exactly once**, by create and by rotate. There is no route that reads
  it back, because the real gateway stores only its SHA-256 and could not answer one.

Unlike the real gateway this mock keeps the key in memory in the clear: there is no database to
hash it into, and nothing here is a credential store. In-memory, resets on restart.
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Header, HTTPException

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

router = APIRouter(prefix="/sessions", tags=["satsang"])

_MOD_ROLES = {"moderator", "admin"}
MOCK_INGEST_BASE = "rtmp://localhost:1935/live"
# How early a speaker may start. No late bound: a satsang that begins 40 minutes behind is still
# that satsang, and refusing a live speaker in front of an audience is the worse failure.
GO_LIVE_EARLY_WINDOW = timedelta(minutes=60)
# What an elder is shown: what is coming, what is on now, and what was called off after a
# reminder may already have gone out. Not `ended` -- the archive is a separate feature.
_ELDER_VISIBLE = (SatsangStatus.SCHEDULED, SatsangStatus.LIVE, SatsangStatus.CANCELLED)

_sessions: dict[str, dict] = {}


def reset() -> None:
    _sessions.clear()


def _name(user_id: str) -> str:
    from contracts.chat.mock import admin_org as mock_admin

    user = mock_admin._users.get(user_id)
    return user.name if user else user_id


def _audit(actor_id: str, action: str, row: dict, **details) -> None:
    """One `admin_actions` row, in the admin mock's log, as the real routes write one in the same
    transaction as the change. Never the stream key or the ingest URL: the log is readable by every
    admin, and the key is shown once."""
    from contracts.chat.admin_org import AdminActionKind
    from contracts.chat.mock import admin_org as mock_admin

    mock_admin._log(
        actor_id, AdminActionKind(action), "session", row["id"], title=row["title"], **details
    )


def _require_moderator(site_role: str | None) -> None:
    if (site_role or "").lower() not in _MOD_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role")


def _row(session_id: str) -> dict:
    row = _sessions.get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


def _admin_view(row: dict) -> SatsangSessionOut:
    return SatsangSessionOut(
        id=row["id"],
        title=row["title"],
        speaker_user_id=row["speaker_user_id"],
        speaker_name=_name(row["speaker_user_id"]),
        scheduled_start_at=row["scheduled_start_at"],
        status=row["status"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        reminder_sent_at=row["reminder_sent_at"],
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _elder_view(row: dict) -> SatsangSessionPublic:
    live = row["status"] is SatsangStatus.LIVE
    return SatsangSessionPublic(
        id=row["id"],
        title=row["title"],
        speaker_name=_name(row["speaker_user_id"]),
        scheduled_start_at=row["scheduled_start_at"],
        status=row["status"],
        hls_url=f"/hls/{row['id']}.m3u8" if live else None,
    )


def _created(row: dict) -> SatsangSessionCreated:
    """The only shape that carries the key -- create and rotate, nothing else."""
    return SatsangSessionCreated(
        session=_admin_view(row),
        stream_key=row["stream_key"],
        ingest_url=f"{MOCK_INGEST_BASE}/{row['id']}?key={row['stream_key']}",
    )


@router.post("", response_model=SatsangSessionCreated, status_code=201)
def schedule_session(
    body: SatsangSessionCreate,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> SatsangSessionCreated:
    _require_moderator(x_mock_role)
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "id": session_id,
        "title": body.title,
        "speaker_user_id": body.speaker_user_id,
        "scheduled_start_at": body.scheduled_start_at,
        "status": SatsangStatus.SCHEDULED,
        "stream_key": secrets.token_urlsafe(STREAM_KEY_BYTES),
        "started_at": None,
        "ended_at": None,
        "reminder_sent_at": None,
        "created_by": x_mock_user_id,
        "created_at": datetime.now(timezone.utc),
    }
    row = _sessions[session_id]
    _audit(
        x_mock_user_id,
        "session.create",
        row,
        speaker_user_id=row["speaker_user_id"],
        scheduled_start_at=row["scheduled_start_at"].isoformat(),
    )
    return _created(row)


@router.get("", response_model=SatsangSessionList)
def list_sessions() -> SatsangSessionList:
    """What an elder sees. Soonest first, so the one that is on now or next is at the top."""
    rows = [r for r in _sessions.values() if r["status"] in _ELDER_VISIBLE]
    rows.sort(key=lambda r: r["scheduled_start_at"])
    return SatsangSessionList(items=[_elder_view(r) for r in rows])


@router.get("/manage", response_model=SatsangSessionAdminList)
def list_sessions_for_console(
    x_mock_role: str | None = Header(default=None),
) -> SatsangSessionAdminList:
    """Every status, newest first -- and still no stream key."""
    _require_moderator(x_mock_role)
    rows = sorted(_sessions.values(), key=lambda r: r["created_at"], reverse=True)
    return SatsangSessionAdminList(items=[_admin_view(r) for r in rows])


@router.post("/{session_id}/cancel", response_model=SatsangSessionOut)
def cancel_session(
    session_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> SatsangSessionOut:
    """Only from `scheduled`. A live satsang is ended, not cancelled."""
    _require_moderator(x_mock_role)
    row = _row(session_id)
    if row["status"] is not SatsangStatus.SCHEDULED:
        raise HTTPException(
            status_code=409, detail=f"a {row['status'].value} satsang cannot be cancelled"
        )
    row["status"] = SatsangStatus.CANCELLED
    # Whether a reminder had gone out: the difference between "nobody noticed" and "people were
    # told to come".
    _audit(x_mock_user_id, "session.cancel", row, reminder_sent=row["reminder_sent_at"] is not None)
    return _admin_view(row)


@router.post("/{session_id}/end", response_model=SatsangSessionOut)
def end_session(
    session_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> SatsangSessionOut:
    """A moderator ends a live satsang by hand: the speaker's phone died and the end-of-stream hook
    never came, or someone has to be cut off. Only from `live`; one that never went on the air is
    cancelled, not ended."""
    _require_moderator(x_mock_role)
    row = _row(session_id)
    if row["status"] is not SatsangStatus.LIVE:
        raise HTTPException(
            status_code=409, detail=f"a {row['status'].value} satsang cannot be ended"
        )
    row["status"] = SatsangStatus.ENDED
    row["ended_at"] = datetime.now(timezone.utc)
    # Only a person ending it is audited; the RTMP server's own end-of-stream hook below is not.
    _audit(x_mock_user_id, "session.end", row)
    return _admin_view(row)


@router.post("/{session_id}/key", response_model=SatsangSessionCreated)
def rotate_stream_key(
    session_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> SatsangSessionCreated:
    """A new key, shown once; the old one stops working at once. That is how a leaked key is dealt
    with, and the only way to recover one nobody wrote down. Open to the satsang's speaker and to a
    moderator; anyone else gets the 404 a missing session gets, body and all (a 403 would confirm
    that a guessable id is real). Not while live: the running stream's end-of-stream hook would then
    carry a key nobody recognises."""
    row = _sessions.get(session_id)
    is_moderator = (x_mock_role or "").lower() in _MOD_ROLES
    if row is None or not (is_moderator or row["speaker_user_id"] == x_mock_user_id):
        raise HTTPException(status_code=404, detail="Session not found")
    if row["status"] is not SatsangStatus.SCHEDULED:
        raise HTTPException(
            status_code=409, detail=f"a {row['status'].value} satsang cannot be re-keyed"
        )
    row["stream_key"] = secrets.token_urlsafe(STREAM_KEY_BYTES)
    _audit(
        x_mock_user_id,
        "session.rotate_key",
        row,
        by="moderator" if is_moderator else "speaker",
    )
    return _created(row)


# --- the ingest hooks: what the RTMP server calls, not what a person taps ----------------------


def _by_name_and_key(name: str, key: str) -> dict:
    """The stream NAME is the session id and the key comes as a separate argument -- what
    nginx-rtmp actually sends for rtmp://host/live/<session id>?key=<key>."""
    row = _sessions.get(name)
    # 403 and not 404, and the same 403 for a wrong session or a wrong key: a caller must not learn
    # whether some session exists, or which half of the pair was wrong.
    if row is None or not key or not secrets.compare_digest(row["stream_key"], key):
        raise HTTPException(status_code=403, detail="unknown stream key")
    return row


@router.post("/ingest/publish", response_model=SatsangSessionOut)
def on_publish(name: str = Body(...), key: str = Body(...)) -> SatsangSessionOut:
    """nginx-rtmp's `on_publish`: a valid key has started pushing audio, so that session is live.

    Refuses if another satsang is already live (the real gateway has a partial unique index for
    this), or if the session is not `scheduled`, or if it is more than an hour early."""
    row = _by_name_and_key(name, key)
    if row["status"] is not SatsangStatus.SCHEDULED:
        raise HTTPException(status_code=409, detail=f"this satsang is {row['status'].value}")
    if any(r["status"] is SatsangStatus.LIVE for r in _sessions.values()):
        raise HTTPException(status_code=409, detail="another satsang is already live")
    now = datetime.now(timezone.utc)
    if now < row["scheduled_start_at"] - GO_LIVE_EARLY_WINDOW:
        raise HTTPException(status_code=409, detail="too early for this satsang")
    row["status"] = SatsangStatus.LIVE
    row["started_at"] = now
    return _admin_view(row)


@router.post("/ingest/done", response_model=SatsangSessionOut)
def on_publish_done(name: str = Body(...), key: str = Body(...)) -> SatsangSessionOut:
    """nginx-rtmp's `on_publish_done`: the speaker stopped, so the session has ended."""
    row = _by_name_and_key(name, key)
    if row["status"] is not SatsangStatus.LIVE:
        raise HTTPException(status_code=409, detail=f"this satsang is {row['status'].value}")
    row["status"] = SatsangStatus.ENDED
    row["ended_at"] = datetime.now(timezone.utc)
    return _admin_view(row)
