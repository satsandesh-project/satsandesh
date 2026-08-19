"""The backbone contract for circles (groups) and announcements.

**This file is the boundary.** ADR 0002 (Matrix vs custom-lite) is still
open past its time-box, so Week 3 builds against this abstraction rather
than against whichever backbone happens to be wired up today. The gateway
depends on this contract only — never on a concrete backbone's internals
— which is what makes the eventual ADR outcome an implementation swap
instead of a gateway rewrite.

Implementations:
  - `backbone/spike-custom-lite/circles.py` -> `OutboxCircleStore`
    (Postgres outbox; the only one that exists today)
  - a Matrix-backed implementation would go here too, if ADR 0002 lands
    that way -- rooms for circles, room membership for members, and a
    room message for an announcement.

Deliberately stdlib-only: no fastapi, no psycopg. Anything that pulls a
concrete backbone's dependencies into this file has broken the boundary.

Async because every implementation we expect is I/O-bound (a database or
an HTTP API). A synchronous implementation can satisfy this trivially;
the reverse is not true, so async is the safer shape to standardise on.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass(frozen=True)
class CircleMessage:
    """One message posted to a circle.

    `id` is the backbone's own identifier and is what `list_messages`
    pages on via `before`. It is opaque to callers: custom-lite uses a
    Postgres BIGSERIAL, and a Matrix implementation would use an event
    ID, so no caller should assume it is numeric or ordered arithmetically
    -- only that `list_messages` returns newest-first and that feeding an
    `id` back as `before` continues from that point.
    """

    id: str
    circle_id: str
    sender_id: str
    body: str
    created_at: datetime


class CircleBackbone(ABC):
    """What any backbone must provide for circles to work.

    Identity note: `user_id` and `sender_id` are caller-asserted strings.
    There is no auth in the system yet (see `gateway/README.md` -- auth is
    the gateway's job and is not built). Implementations should not invent
    their own identity model to fill that gap.
    """

    @abstractmethod
    async def create_circle(self, name: str) -> str:
        """Create a circle and return its id."""

    @abstractmethod
    async def add_member(self, circle_id: str, user_id: str) -> None:
        """Add a member. Idempotent: adding an existing member is not an
        error, so callers don't have to check-then-add (which would race)."""

    @abstractmethod
    async def remove_member(self, circle_id: str, user_id: str) -> None:
        """Remove a member. Idempotent, for the same reason.

        Removal affects *future* announcements only. Anything already
        written for delivery stays owed to that user -- see
        `post_announcement`."""

    @abstractmethod
    async def list_members(self, circle_id: str) -> List[str]:
        """Current members, as user ids."""

    @abstractmethod
    async def post_announcement(self, circle_id: str, sender_id: str, body: str) -> str:
        """Post one message to every current member; return its message id.

        Membership is resolved to a recipient list **at post time**, and
        that resolution is what each member is owed. A member added a
        second later does not receive it; a member removed a second later
        still does, because the obligation was already recorded. That is a
        deliberate semantic, not an accident of implementation -- it's
        what makes delivery durable rather than dependent on membership
        still being unchanged whenever delivery happens to occur.

        Sender need not be a member (an announcement channel is
        one-to-many by design).
        """

    @abstractmethod
    async def list_messages(
        self, circle_id: str, limit: int = 50, before: Optional[str] = None
    ) -> List[CircleMessage]:
        """Message history, newest first.

        `before`: return only messages older than this message id, for
        paging backwards. `None` starts from the newest.
        """
