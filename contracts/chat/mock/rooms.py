"""
`/rooms/*` in the mock chat gateway: what the elder app's bhajan-room screens build
against before the real routes exist.

Same rules as the real gateway, so a client written against this works there:
only a circle's moderator (a circle moderator or admin, or a site moderator or admin)
creates and closes rooms and skips people; anyone in the circle may raise a hand; exactly
one person leads at a time; the lead (or a moderator) hands over with "next", and when
nobody is leading the first person waiting may take the floor; a token allows publishing
only for the current lead. A person who is not in the room's circle gets 404, as if the
room did not exist. No LiveKit here: the token is a placeholder. In-memory, resets on
restart.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Query

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

router = APIRouter(prefix="/rooms", tags=["rooms"])

_MOD_ROLES = {"moderator", "admin"}
MOCK_LIVEKIT_URL = "ws://localhost:7880"

_rooms: dict[str, RoomOut] = {}
# room id -> entries in the order they raised their hand; state says who is waiting / leading.
_entries: dict[str, list[dict]] = {}


def reset() -> None:
    _rooms.clear()
    _entries.clear()


def _stores():
    from contracts.chat.mock import app as main_mock

    return main_mock


def _name(user_id: str) -> str:
    from contracts.chat.mock import admin_org as mock_admin

    user = mock_admin._users.get(user_id)
    return user.name if user else user_id


def _circle_role(circle_id: str, user_id: str) -> str | None:
    for membership in _stores()._memberships.get(circle_id, []):
        if membership.user_id == user_id:
            return membership.role.value
    return None


def _is_member(circle_id: str, user_id: str, site_role: str | None) -> bool:
    return _circle_role(circle_id, user_id) is not None or (site_role or "").lower() in _MOD_ROLES


def _is_moderator(circle_id: str, user_id: str, site_role: str | None) -> bool:
    return (site_role or "").lower() in _MOD_ROLES or _circle_role(circle_id, user_id) in _MOD_ROLES


def _room(room_id: str, user_id: str, site_role: str | None) -> RoomOut:
    room = _rooms.get(room_id)
    if room is None or not _is_member(room.circle_id, user_id, site_role):
        raise HTTPException(status_code=404, detail="Room not found")
    return room


def _lead_entry(room_id: str) -> dict | None:
    return next((e for e in _entries.get(room_id, []) if e["state"] is QueueState.LEADING), None)


def _waiting(room_id: str) -> list[dict]:
    return [e for e in _entries.get(room_id, []) if e["state"] is QueueState.WAITING]


def _person(entry: dict | None) -> RoomPerson | None:
    return RoomPerson(user_id=entry["user_id"], name=_name(entry["user_id"])) if entry else None


def _room_out(room: RoomOut) -> RoomOut:
    return room.model_copy(
        update={"lead": _person(_lead_entry(room.id)), "waiting_count": len(_waiting(room.id))}
    )


def _state(room: RoomOut, user_id: str, site_role: str | None) -> RoomState:
    lead = _lead_entry(room.id)
    waiting = _waiting(room.id)
    # The caller's most recent turn in the queue, whatever became of it (waiting, leading, had a
    # turn, skipped), so the app can say "you have had your turn" without keeping its own memory.
    mine = next((e for e in reversed(_entries.get(room.id, [])) if e["user_id"] == user_id), None)
    is_open = room.status is RoomStatus.OPEN
    manage = is_open and _is_moderator(room.circle_id, user_id, site_role)
    is_lead = bool(lead and lead["user_id"] == user_id)
    first_in_line = bool(lead is None and waiting and waiting[0]["user_id"] == user_id)
    return RoomState(
        room=_room_out(room),
        lead=_person(lead),
        queue=[
            QueueEntryOut(user_id=e["user_id"], name=_name(e["user_id"]), position=i + 1)
            for i, e in enumerate(waiting)
        ],
        you=YourPlace(
            state=mine["state"] if mine else None,
            position=(waiting.index(mine) + 1) if mine and mine in waiting else None,
            is_lead=is_lead,
            can_take_floor=is_open
            and (is_lead or manage or first_in_line)
            and bool(lead or waiting),
            can_manage=manage,
        ),
    )


def _hand_over(room_id: str) -> None:
    """The lead (if any) is done and the first waiting person leads."""
    lead = _lead_entry(room_id)
    if lead is not None:
        lead["state"] = QueueState.DONE
    waiting = _waiting(room_id)
    if waiting:
        waiting[0]["state"] = QueueState.LEADING


@router.post("", response_model=RoomOut, status_code=201)
def create_room(
    body: RoomCreate,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomOut:
    if body.circle_id not in _stores()._circles:
        raise HTTPException(status_code=404, detail="Circle not found")
    if not _is_moderator(body.circle_id, x_mock_user_id, x_mock_role):
        raise HTTPException(status_code=403, detail="Insufficient role")
    room_id = str(uuid.uuid4())
    room = RoomOut(
        id=room_id,
        circle_id=body.circle_id,
        title=body.title,
        status=RoomStatus.OPEN,
        created_by=x_mock_user_id,
        created_at=datetime.now(timezone.utc),
        livekit_room=f"room-{room_id}",
    )
    _rooms[room_id] = room
    _entries[room_id] = []
    return _room_out(room)


@router.get("", response_model=RoomList)
def list_rooms(
    circle_id: str | None = Query(default=None),
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomList:
    rooms = [
        r
        for r in _rooms.values()
        if r.status is RoomStatus.OPEN
        and (circle_id is None or r.circle_id == circle_id)
        and _is_member(r.circle_id, x_mock_user_id, x_mock_role)
    ]
    rooms.sort(key=lambda r: r.created_at, reverse=True)
    return RoomList(items=[_room_out(r) for r in rooms])


@router.get("/{room_id}", response_model=RoomState)
def get_room(
    room_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomState:
    return _state(_room(room_id, x_mock_user_id, x_mock_role), x_mock_user_id, x_mock_role)


def _open_room(room_id: str, user_id: str, site_role: str | None) -> RoomOut:
    room = _room(room_id, user_id, site_role)
    if room.status is not RoomStatus.OPEN:
        raise HTTPException(status_code=409, detail="This room is closed")
    return room


@router.post("/{room_id}/token", response_model=RoomTokenOut)
def room_token(
    room_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomTokenOut:
    room = _open_room(room_id, x_mock_user_id, x_mock_role)
    lead = _lead_entry(room.id)
    return RoomTokenOut(
        token=f"mock-livekit-token-{room.id}-{x_mock_user_id}",
        url=MOCK_LIVEKIT_URL,
        identity=x_mock_user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        can_publish=bool(lead and lead["user_id"] == x_mock_user_id),
    )


@router.post("/{room_id}/queue", response_model=RoomState)
def raise_hand(
    room_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomState:
    room = _open_room(room_id, x_mock_user_id, x_mock_role)
    already = any(
        e["user_id"] == x_mock_user_id and e["state"] in (QueueState.WAITING, QueueState.LEADING)
        for e in _entries[room.id]
    )
    if not already:
        _entries[room.id].append({"user_id": x_mock_user_id, "state": QueueState.WAITING})
    return _state(room, x_mock_user_id, x_mock_role)


@router.delete("/{room_id}/queue", response_model=RoomState)
def lower_hand(
    room_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomState:
    room = _open_room(room_id, x_mock_user_id, x_mock_role)
    for entry in _entries[room.id]:
        if entry["user_id"] != x_mock_user_id:
            continue
        if entry["state"] is QueueState.LEADING:
            _hand_over(room.id)  # the lead leaving is a handoff
        elif entry["state"] is QueueState.WAITING:
            entry["state"] = QueueState.DONE
    return _state(room, x_mock_user_id, x_mock_role)


@router.post("/{room_id}/next", response_model=RoomState)
def next_lead(
    room_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomState:
    room = _open_room(room_id, x_mock_user_id, x_mock_role)
    state = _state(room, x_mock_user_id, x_mock_role)
    if state.lead is None and not state.queue:
        raise HTTPException(status_code=409, detail="Nobody is waiting")
    if not state.you.can_take_floor:
        raise HTTPException(status_code=403, detail="Only the lead or a moderator can hand over")
    _hand_over(room.id)
    return _state(room, x_mock_user_id, x_mock_role)


@router.post("/{room_id}/queue/{user_id}/skip", response_model=RoomState)
def skip_person(
    room_id: str,
    user_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomState:
    room = _open_room(room_id, x_mock_user_id, x_mock_role)
    if not _is_moderator(room.circle_id, x_mock_user_id, x_mock_role):
        raise HTTPException(status_code=403, detail="Insufficient role")
    entry = next((e for e in _waiting(room.id) if e["user_id"] == user_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="That person is not waiting")
    entry["state"] = QueueState.REMOVED
    return _state(room, x_mock_user_id, x_mock_role)


@router.post("/{room_id}/close", response_model=RoomState)
def close_room(
    room_id: str,
    x_mock_user_id: str = Header(default="mock-user-1"),
    x_mock_role: str | None = Header(default=None),
) -> RoomState:
    room = _room(room_id, x_mock_user_id, x_mock_role)
    if not _is_moderator(room.circle_id, x_mock_user_id, x_mock_role):
        raise HTTPException(status_code=403, detail="Insufficient role")
    if room.status is RoomStatus.OPEN:
        closed = room.model_copy(
            update={"status": RoomStatus.CLOSED, "closed_at": datetime.now(timezone.utc)}
        )
        _rooms[room.id] = closed
        for entry in _entries[room.id]:
            if entry["state"] in (QueueState.WAITING, QueueState.LEADING):
                entry["state"] = QueueState.DONE
        room = closed
    return _state(room, x_mock_user_id, x_mock_role)
