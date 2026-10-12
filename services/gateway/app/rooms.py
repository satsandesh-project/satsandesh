"""Bhajan rooms with floor control (`/rooms/*`, `contracts/chat/rooms.py`).

A room is a live audio space for a circle, for about 50 people, where exactly one microphone is
live: the lead singer's. Audio runs on LiveKit (app/livekit.py); this module owns the rules about
WHO may speak and enforces them on the server:

- A join token says `canPublish: true` only for the person leading at that moment.
- When the lead changes, the gateway changes the connected people's permission through LiveKit's
  admin API: the old lead is silenced FIRST, then the new lead is allowed. If LiveKit cannot be
  reached, the whole hand-over is rolled back (502) rather than leaving the database and the audio
  disagreeing about who may speak.
- The database allows at most one lead per room (a partial unique index), so even a race between
  two requests cannot put two live microphones in a hall. Every change to who leads takes the room
  row lock first, so two hand-overs at once are serialised.

Who may do what:

- Create a room, close it, skip someone in the queue, hand over at any time: a **moderator**, meaning
  a circle moderator or admin, or a site moderator or admin (`users.role`).
- See a room, raise a hand, lower it, get a token: anyone in the room's circle. A person who is
  not gets 404, as if the room did not exist.
- Hand over ("next"): the current lead, a moderator, or -- when nobody is leading -- the first
  person waiting.

Identity is the gateway's normal one (`get_current_user`), like circles and messages. Until the
app holds signed tokens (OPEN_QUESTIONS #32), a bare UUID is that user in the default
`AUTH_MODE`, so a moderator's id is enough to create or close a room. That is a smaller risk than
the console routes (`/moderation`, `/admin`), which refuse bare UUIDs; it is written down in
OPEN_QUESTIONS and should be tightened when the app has signed tokens.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from contracts.chat.rooms import (
    QueueEntryOut,
    QueueState,
    RoomCreate,
    RoomList,
    RoomOut,
    RoomPerson,
    RoomState,
    RoomStatus,
    RoomTokenOut,
    YourPlace,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import livekit
from app.auth import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Circle, Membership
from app.db.models import User as DbUser
from app.db.rooms import (
    LeadQueueEntry,
    Room,
    latest_entry,
    lead_entry,
    lock_room,
    next_position,
    waiting_entries,
)
from app.livekit import LiveKitUnavailable
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rooms", tags=["rooms"])

_MOD_ROLES = ("moderator", "admin")
_NOT_FOUND = HTTPException(status_code=404, detail="Room not found")


# --- who the caller is, and what they may do ---------------------------------------------------


def _caller(db: Session, user: User) -> DbUser:
    try:
        row = db.get(DbUser, uuid.UUID(user.id))
    except ValueError:  # a non-UUID stub token has no row
        row = None
    if row is None:
        raise HTTPException(status_code=403, detail="Unknown user")
    return row


def _circle_role(db: Session, circle_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    return db.execute(
        select(Membership.role).where(
            Membership.circle_id == circle_id, Membership.user_id == user_id
        )
    ).scalar_one_or_none()


def _is_moderator(db: Session, circle_id: uuid.UUID, caller: DbUser) -> bool:
    return caller.role in _MOD_ROLES or _circle_role(db, circle_id, caller.id) in _MOD_ROLES


def _can_see(db: Session, circle_id: uuid.UUID, caller: DbUser) -> bool:
    return caller.role in _MOD_ROLES or _circle_role(db, circle_id, caller.id) is not None


def _uuid_or_404(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _NOT_FOUND from None


def _visible_room(db: Session, room_id: str, caller: DbUser, *, lock: bool = False) -> Room:
    """The room, or 404 if it does not exist or the caller is not in its circle."""
    rid = _uuid_or_404(room_id)
    room = lock_room(db, rid) if lock else db.get(Room, rid)
    if room is None or not _can_see(db, room.circle_id, caller):
        raise _NOT_FOUND
    return room


def _open_room(room: Room) -> Room:
    if room.status != "open":
        raise HTTPException(status_code=409, detail="This room is closed")
    return room


# --- building the response -----------------------------------------------------------------------


def _names(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    return dict(db.execute(select(DbUser.id, DbUser.name).where(DbUser.id.in_(user_ids))).all())


def _person(user_id: uuid.UUID, names: dict[uuid.UUID, str]) -> RoomPerson:
    return RoomPerson(user_id=str(user_id), name=names.get(user_id, str(user_id)))


def _room_out(
    room: Room,
    *,
    lead: LeadQueueEntry | None,
    waiting: list[LeadQueueEntry],
    names: dict[uuid.UUID, str],
) -> RoomOut:
    return RoomOut(
        id=str(room.id),
        circle_id=str(room.circle_id),
        title=room.title,
        status=RoomStatus(room.status),
        created_by=str(room.created_by),
        created_at=room.created_at,
        closed_at=room.closed_at,
        livekit_room=room.livekit_room,
        lead=_person(lead.user_id, names) if lead else None,
        waiting_count=len(waiting),
    )


def _state(db: Session, room: Room, caller: DbUser) -> RoomState:
    lead = lead_entry(db, room.id)
    waiting = waiting_entries(db, room.id)
    mine = latest_entry(db, room.id, caller.id)
    ids = [e.user_id for e in waiting] + ([lead.user_id] if lead else [])
    names = _names(db, ids)

    is_open = room.status == "open"
    manage = is_open and _is_moderator(db, room.circle_id, caller)
    is_lead = bool(lead and lead.user_id == caller.id)
    first_in_line = bool(lead is None and waiting and waiting[0].user_id == caller.id)
    position = None
    if mine is not None and mine.state == "waiting":
        position = next((i + 1 for i, e in enumerate(waiting) if e.id == mine.id), None)
    return RoomState(
        room=_room_out(room, lead=lead, waiting=waiting, names=names),
        lead=_person(lead.user_id, names) if lead else None,
        queue=[
            QueueEntryOut(user_id=str(e.user_id), name=names.get(e.user_id, ""), position=i + 1)
            for i, e in enumerate(waiting)
        ],
        you=YourPlace(
            state=QueueState(mine.state) if mine else None,
            position=position,
            is_lead=is_lead,
            can_take_floor=is_open
            and (is_lead or manage or first_in_line)
            and bool(lead or waiting),
            can_manage=manage,
        ),
    )


# --- changing who leads ----------------------------------------------------------------------------


def _hand_over(db: Session, room: Room) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """The lead (if any) is done and the first person waiting leads. Returns (old, new) user ids.
    The caller holds the room lock and commits."""
    now = datetime.now(UTC)
    lead = lead_entry(db, room.id)
    waiting = waiting_entries(db, room.id)
    old_id = lead.user_id if lead else None
    if lead is not None:
        lead.state = "done"
        lead.ended_at = now
        db.flush()  # the old lead is out before the new one is in: one lead at a time, always
    new = waiting[0] if waiting else None
    if new is not None:
        new.state = "leading"
        new.started_at = now
        db.flush()
    return old_id, (new.user_id if new else None)


def _sync_audio(
    db: Session, room: Room, *, old_id: uuid.UUID | None, new_id: uuid.UUID | None
) -> None:
    """Make LiveKit agree with the database: silence the old lead FIRST, then allow the new one.

    A failure rolls the whole hand-over back (the caller's transaction) and is a 502, so the
    database and the audio never disagree about who may speak. If silencing worked but allowing
    the new lead failed, the old lead is allowed again, best effort, before giving up."""
    admin = livekit.get_livekit_admin()
    if admin is None:
        return
    try:
        if old_id is not None:
            admin.set_can_publish(room.livekit_room, str(old_id), False)
        if new_id is not None:
            try:
                admin.set_can_publish(room.livekit_room, str(new_id), True)
            except LiveKitUnavailable:
                if old_id is not None:
                    try:
                        admin.set_can_publish(room.livekit_room, str(old_id), True)
                    except LiveKitUnavailable:
                        logger.error("room %s: could not restore the previous lead", room.id)
                raise
    except LiveKitUnavailable as exc:
        db.rollback()
        logger.warning("room %s: hand-over rolled back: %s", room.id, exc)
        raise HTTPException(
            status_code=502, detail="Could not change the microphone just now. Please try again."
        ) from exc
    logger.info("room %s: lead %s -> %s", room.id, old_id, new_id)


# --- routes ----------------------------------------------------------------------------------------------


@router.post("", response_model=RoomOut, status_code=201)
def create_room(
    body: RoomCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomOut:
    caller = _caller(db, user)
    try:
        circle = db.get(Circle, uuid.UUID(body.circle_id))
    except ValueError:
        circle = None
    if circle is None:
        raise HTTPException(status_code=404, detail="Circle not found")
    if not _is_moderator(db, circle.id, caller):
        raise HTTPException(status_code=403, detail="Insufficient role")
    room_id = uuid.uuid4()
    room = Room(
        id=room_id,
        circle_id=circle.id,
        title=body.title,
        livekit_room=livekit.livekit_room_name(room_id),
        created_by=caller.id,
    )
    db.add(room)
    db.commit()
    db.refresh(room)
    return _room_out(room, lead=None, waiting=[], names={})


@router.get("", response_model=RoomList)
def list_rooms(
    circle_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomList:
    caller = _caller(db, user)
    stmt = select(Room).where(Room.status == "open")
    if circle_id is not None:
        stmt = stmt.where(Room.circle_id == _uuid_or_404(circle_id))
    if caller.role not in _MOD_ROLES:
        stmt = stmt.where(
            Room.circle_id.in_(select(Membership.circle_id).where(Membership.user_id == caller.id))
        )
    rooms = list(db.execute(stmt.order_by(Room.created_at.desc(), Room.id.desc())).scalars())
    out = []
    for room in rooms:
        lead = lead_entry(db, room.id)
        waiting = waiting_entries(db, room.id)
        ids = [e.user_id for e in waiting] + ([lead.user_id] if lead else [])
        out.append(_room_out(room, lead=lead, waiting=waiting, names=_names(db, ids)))
    return RoomList(items=out)


@router.get("/{room_id}", response_model=RoomState)
def get_room(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomState:
    caller = _caller(db, user)
    return _state(db, _visible_room(db, room_id, caller), caller)


@router.post("/{room_id}/token", response_model=RoomTokenOut)
def room_token(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomTokenOut:
    caller = _caller(db, user)
    room = _open_room(_visible_room(db, room_id, caller))
    admin = livekit.get_livekit_admin()
    if admin is None:
        raise HTTPException(status_code=503, detail="Audio rooms are not set up on this server")
    # Publishing is allowed only for whoever leads at this moment. Read under no lock: a hand-over
    # right after this changes the permission of a connected person through LiveKit anyway.
    lead = lead_entry(db, room.id)
    can_publish = bool(lead and lead.user_id == caller.id)
    try:
        admin.create_room(room.livekit_room)  # idempotent; applies the 50-person limit
    except LiveKitUnavailable as exc:
        raise HTTPException(
            status_code=502, detail="The audio room is not reachable just now"
        ) from exc
    token, expires = livekit.join_token(
        identity=str(caller.id),
        name=caller.name,
        room=room.livekit_room,
        can_publish=can_publish,
    )
    return RoomTokenOut(
        token=token,
        url=get_settings().LIVEKIT_PUBLIC_URL or "",
        identity=str(caller.id),
        expires_at=expires,
        can_publish=can_publish,
    )


@router.post("/{room_id}/queue", response_model=RoomState)
def raise_hand(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomState:
    caller = _caller(db, user)
    room = _open_room(_visible_room(db, room_id, caller, lock=True))
    mine = latest_entry(db, room.id, caller.id)
    if mine is None or mine.state not in ("waiting", "leading"):
        db.add(
            LeadQueueEntry(room_id=room.id, user_id=caller.id, position=next_position(db, room.id))
        )
        db.commit()
    return _state(db, room, caller)


@router.delete("/{room_id}/queue", response_model=RoomState)
def lower_hand(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomState:
    caller = _caller(db, user)
    room = _open_room(_visible_room(db, room_id, caller, lock=True))
    mine = latest_entry(db, room.id, caller.id)
    if mine is not None and mine.state == "waiting":
        mine.state = "done"
        mine.ended_at = datetime.now(UTC)
        db.commit()
    elif mine is not None and mine.state == "leading":
        old_id, new_id = _hand_over(db, room)  # the lead leaving is a hand-over
        _sync_audio(db, room, old_id=old_id, new_id=new_id)
        db.commit()
    return _state(db, room, caller)


@router.post("/{room_id}/next", response_model=RoomState)
def next_lead(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomState:
    caller = _caller(db, user)
    room = _open_room(_visible_room(db, room_id, caller, lock=True))
    lead = lead_entry(db, room.id)
    waiting = waiting_entries(db, room.id)
    if lead is None and not waiting:
        raise HTTPException(status_code=409, detail="Nobody is waiting")
    allowed = (
        (lead is not None and lead.user_id == caller.id)
        or _is_moderator(db, room.circle_id, caller)
        or (lead is None and waiting[0].user_id == caller.id)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Only the lead or a moderator can hand over")
    old_id, new_id = _hand_over(db, room)
    _sync_audio(db, room, old_id=old_id, new_id=new_id)
    db.commit()
    return _state(db, room, caller)


@router.post("/{room_id}/queue/{user_id}/skip", response_model=RoomState)
def skip_person(
    room_id: str,
    user_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomState:
    caller = _caller(db, user)
    room = _open_room(_visible_room(db, room_id, caller, lock=True))
    if not _is_moderator(db, room.circle_id, caller):
        raise HTTPException(status_code=403, detail="Insufficient role")
    try:
        target = uuid.UUID(user_id)
    except ValueError:
        target = None
    entry = next((e for e in waiting_entries(db, room.id) if e.user_id == target), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="That person is not waiting")
    entry.state = "removed"
    entry.ended_at = datetime.now(UTC)
    db.commit()
    return _state(db, room, caller)


@router.post("/{room_id}/close", response_model=RoomState)
def close_room(
    room_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RoomState:
    caller = _caller(db, user)
    room = _visible_room(db, room_id, caller, lock=True)
    if not _is_moderator(db, room.circle_id, caller):
        raise HTTPException(status_code=403, detail="Insufficient role")
    if room.status == "open":
        now = datetime.now(UTC)
        room.status = "closed"
        room.closed_at = now
        for entry in db.execute(
            select(LeadQueueEntry).where(
                LeadQueueEntry.room_id == room.id,
                LeadQueueEntry.state.in_(("waiting", "leading")),
            )
        ).scalars():
            entry.state = "done"
            entry.ended_at = now
        db.flush()
        admin = livekit.get_livekit_admin()
        if admin is not None:
            try:
                admin.delete_room(room.livekit_room)  # disconnects everyone, lead included
            except LiveKitUnavailable as exc:
                db.rollback()
                raise HTTPException(
                    status_code=502, detail="Could not close the audio just now. Please try again."
                ) from exc
        db.commit()
    return _state(db, room, caller)
