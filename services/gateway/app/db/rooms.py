"""Bhajan rooms with floor control: the `rooms` and `lead_queue` tables.

A separate module, not more of app/db/models.py, so the feature owns its own storage. Anything
that builds the schema from `Base.metadata` (the test fixtures' cleanup) sees these tables once
this module is imported -- the routes that use them (app/rooms.py) import it.

The one rule the database itself enforces: **at most one person leads a room**. `lead_queue` has
a partial unique index on `room_id` where `state = 'leading'`, so a bug (or a race between two
requests) that tried to make two leads fails on the index instead of putting two live microphones
in a hall. A second partial unique index keeps a person from being in the queue twice.

The functions here do NOT commit and take no locks of their own: the caller (app/rooms.py)
locks the room row (`lock_room`) before changing who leads, so two hand-overs at once are
serialised and the second sees the first's result.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.id import generate_uuid7

ROOM_STATUSES = ("open", "closed")
QUEUE_STATES = ("waiting", "leading", "done", "removed")


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_rooms_status"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="ck_rooms_title_length"),
        sa.CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name="ck_rooms_closed_at_iff_closed"
        ),
        sa.Index("ix_rooms_circle_id_status", "circle_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    circle_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("circles.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # The LiveKit room name. Derived from the id and unique, so two rooms can never share audio.
    livekit_room: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="open")
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class LeadQueueEntry(Base):
    __tablename__ = "lead_queue"
    __table_args__ = (
        sa.CheckConstraint(
            "state IN ('waiting', 'leading', 'done', 'removed')", name="ck_lead_queue_state"
        ),
        # Exactly one microphone: never two leads in one room, enforced by the database.
        sa.Index(
            "uq_lead_queue_one_lead",
            "room_id",
            unique=True,
            postgresql_where=sa.text("state = 'leading'"),
        ),
        # A person is in the queue at most once at a time (they may queue again after a turn).
        sa.Index(
            "uq_lead_queue_one_active_place",
            "room_id",
            "user_id",
            unique=True,
            postgresql_where=sa.text("state IN ('waiting', 'leading')"),
        ),
        sa.Index("ix_lead_queue_room_state_position", "room_id", "state", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=generate_uuid7
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Order of raising a hand, within the room. Never reused or renumbered; who is "next" is the
    # waiting entry with the lowest position.
    position: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(sa.Text, nullable=False, server_default="waiting")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


def lock_room(session: Session, room_id: uuid.UUID) -> Room | None:
    """The room row, locked until the transaction ends (`FOR UPDATE`). Every change to who
    leads takes this first, so two hand-overs at the same moment are serialised."""
    return session.execute(
        select(Room).where(Room.id == room_id).with_for_update()
    ).scalar_one_or_none()


def lead_entry(session: Session, room_id: uuid.UUID) -> LeadQueueEntry | None:
    return session.execute(
        select(LeadQueueEntry).where(
            LeadQueueEntry.room_id == room_id, LeadQueueEntry.state == "leading"
        )
    ).scalar_one_or_none()


def waiting_entries(session: Session, room_id: uuid.UUID) -> list[LeadQueueEntry]:
    """Who is waiting, next first."""
    return list(
        session.execute(
            select(LeadQueueEntry)
            .where(LeadQueueEntry.room_id == room_id, LeadQueueEntry.state == "waiting")
            .order_by(LeadQueueEntry.position, LeadQueueEntry.id)
        ).scalars()
    )


def latest_entry(session: Session, room_id: uuid.UUID, user_id: uuid.UUID) -> LeadQueueEntry | None:
    """The caller's most recent turn in the queue, whatever became of it."""
    return session.execute(
        select(LeadQueueEntry)
        .where(LeadQueueEntry.room_id == room_id, LeadQueueEntry.user_id == user_id)
        .order_by(LeadQueueEntry.position.desc())
        .limit(1)
    ).scalar_one_or_none()


def next_position(session: Session, room_id: uuid.UUID) -> int:
    top = session.execute(
        select(sa.func.max(LeadQueueEntry.position)).where(LeadQueueEntry.room_id == room_id)
    ).scalar_one()
    return (top or 0) + 1
