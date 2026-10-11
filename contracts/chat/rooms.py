"""
Bhajan rooms with floor control (`/rooms/*`).

A room is a live audio space for a circle, for up to about 50 people, where
exactly ONE microphone is live: the lead singer's. One leads, everyone else
listens and follows, as in a hall. Audio itself runs on LiveKit; the gateway owns
the rules about WHO may speak, and enforces them on the server (a token that says
`canPublish: false` for everyone but the current lead, and a permission update
when the lead changes), never on the client.

How a turn works, so the shapes below read naturally:

- Anyone in the room's circle may **raise a hand**: they join the queue, in order.
- The **lead** is the person whose turn it is. When nobody is leading, the first
  person waiting may take the floor themselves; otherwise the current lead (or a
  moderator) hands over with "next", which makes the first waiting person lead and
  silences the previous lead. The handoff is one request.
- A moderator (a circle moderator or admin, or a site moderator or admin) may
  create and close rooms, skip anyone in the queue, and hand over at any time.
- A person can leave the queue; the lead leaving is a handoff to the next.

The state of a room is small, so the app asks for it again every second or two
(`GET /rooms/{id}`) instead of the gateway pushing it; nothing here touches the
WebSocket frame types.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from contracts.chat.common import VersionedModel

MAX_TITLE_LENGTH = 120
# The room is built for about this many people. LiveKit enforces it for real.
MAX_PARTICIPANTS = 50


class RoomStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class QueueState(str, Enum):
    WAITING = "waiting"  # hand raised, not yet their turn
    LEADING = "leading"  # the one live microphone
    DONE = "done"  # had a turn, or left
    REMOVED = "removed"  # skipped by a moderator


class RoomPerson(VersionedModel):
    user_id: str
    name: str


class RoomCreate(VersionedModel):
    """Request body for `POST /rooms`. Moderators only."""

    circle_id: str
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class RoomOut(VersionedModel):
    id: str
    circle_id: str
    title: str
    status: RoomStatus
    created_by: str
    created_at: datetime
    closed_at: datetime | None = None
    # The LiveKit room name. The app does not need it (the token carries it); it is here so a
    # developer reading the response can find the room in LiveKit's own tools.
    livekit_room: str
    lead: RoomPerson | None = None
    waiting_count: int = 0


class RoomList(VersionedModel):
    """Response for `GET /rooms?circle_id=`: the open rooms of the circles the caller is in
    (or of one circle), newest first."""

    items: list[RoomOut]


class QueueEntryOut(VersionedModel):
    user_id: str
    name: str
    # 1 for the person who is next, 2 for the one after, and so on. Only waiting people.
    position: int = Field(ge=1)
    state: QueueState = QueueState.WAITING


class YourPlace(VersionedModel):
    """The caller's own standing in the room, so the app can draw the right button without
    working it out from the queue."""

    # The caller's most recent turn in the queue, whatever became of it; None if they never raised
    # a hand in this room.
    state: QueueState | None = None
    position: int | None = Field(default=None, ge=1)  # set while waiting
    is_lead: bool = False
    # May the caller press "Take the next bhajan" right now: they are the lead, or a moderator,
    # or nobody is leading and they are first in line.
    can_take_floor: bool = False
    # May the caller skip people, hand over at any time and close the room.
    can_manage: bool = False


class RoomState(VersionedModel):
    """Response for `GET /rooms/{id}` (and for every queue action): everything the room screen
    draws. Polling this is how the app learns the lead changed."""

    room: RoomOut
    lead: RoomPerson | None = None
    queue: list[QueueEntryOut] = Field(default_factory=list)
    you: YourPlace


class RoomTokenOut(VersionedModel):
    """Response for `POST /rooms/{id}/token`: what the app needs to join the audio room.

    `can_publish` is true only if the caller is the current lead when the token is issued. The
    gateway also changes a connected person's permission when the lead changes, so the app does
    not need a new token for a handoff; it only needs one to (re)join."""

    token: str
    url: str  # the LiveKit signalling URL the app connects to (wss://...)
    identity: str
    expires_at: datetime
    can_publish: bool = False
