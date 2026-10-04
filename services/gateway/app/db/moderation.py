"""Reading and writing the append-only moderation trail (`moderation_events`).

A separate module, not more of app/db/repository.py: this is new surface and
nothing in it needs the existing functions. Same rules as that file -- plain
functions over a Session, no framework, the caller owns commit/rollback.

There is deliberately no update or delete here. A release, a block and a
later reversal are each a new event; the table's trigger would refuse the
alternative anyway (app/db/models.py::ModerationEvent).

Not here yet, by design: the mapping from an action to a message status. The
chat contract's OPEN_QUESTIONS (#8 and the moderation section's #1) leave it
open, and it belongs with the orchestrator that acts on it.
"""

from __future__ import annotations

import uuid

from contracts.ai.moderation import ModerationDecision
from contracts.chat import moderation as chat_moderation
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Message, ModerationEvent, User


def record_moderation_event(
    session: Session,
    *,
    message_id: uuid.UUID,
    actor_kind: str,
    label: str,
    action: str,
    rationale: str,
    policy_version: str,
    actor_id: uuid.UUID | None = None,
    confidence: float | None = None,
    note: str | None = None,
    notice_text: str | None = None,
    model_version: str | None = None,
    degraded: bool = False,
    degraded_reason: str | None = None,
) -> ModerationEvent:
    """Append one event. The actor rules are checked here so the caller gets
    a readable error; the table's CHECK constraints are the backstop for a
    writer that bypasses this function."""
    if (actor_kind == "moderator") != (actor_id is not None):
        raise ValueError(
            "actor_id must be set exactly for a moderator event "
            f"(actor_kind={actor_kind!r}, actor_id={actor_id!r})"
        )
    if actor_kind != "classifier" and (confidence is not None or model_version is not None):
        raise ValueError(
            "only a classifier event has a confidence or a model_version "
            "(a person's decision has no model opinion)"
        )
    event = ModerationEvent(
        message_id=message_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        label=label,
        action=action,
        confidence=confidence,
        rationale=rationale,
        note=note,
        notice_text=notice_text,
        policy_version=policy_version,
        model_version=model_version,
        degraded=degraded,
        degraded_reason=degraded_reason,
    )
    session.add(event)
    session.flush()
    return event


def record_classifier_decision(
    session: Session,
    *,
    message_id: uuid.UUID,
    decision: ModerationDecision,
    notice_text: str | None = None,
) -> ModerationEvent:
    """Store the classifier's answer as the classifier's event.

    `notice_text` is what the sender was ACTUALLY told, in their own language,
    and is recorded only when the caller passes it -- the decision's own
    `nudge_text` is an English master (the mock's is Telugu), not necessarily
    what was sent, so it is not copied across.

    The AI contract's `degraded` is {active, reason, detail}; the chat
    contract's is a bool. The bool is `active`; the reason is kept so whoever
    reads the trail can tell a timeout from an unparseable reply."""
    degraded = decision.degraded.active
    return record_moderation_event(
        session,
        message_id=message_id,
        actor_kind="classifier",
        label=decision.label.value,
        action=decision.action.value,
        confidence=decision.confidence,
        rationale=decision.rationale,
        notice_text=notice_text,
        policy_version=decision.policy_version,
        model_version=decision.model_version,
        degraded=degraded,
        degraded_reason=decision.degraded.reason.value if degraded else None,
    )


def list_moderation_events(session: Session, message_id: uuid.UUID) -> list[ModerationEvent]:
    """The message's trail, oldest first."""
    return list(
        session.scalars(
            select(ModerationEvent)
            .where(ModerationEvent.message_id == message_id)
            .order_by(ModerationEvent.created_at, ModerationEvent.id)
        )
    )


def latest_moderation_event(session: Session, message_id: uuid.UUID) -> ModerationEvent | None:
    return session.scalars(
        select(ModerationEvent)
        .where(ModerationEvent.message_id == message_id)
        .order_by(ModerationEvent.created_at.desc(), ModerationEvent.id.desc())
        .limit(1)
    ).first()


def count_moderation_events(session: Session, message_id: uuid.UUID) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(ModerationEvent)
            .where(ModerationEvent.message_id == message_id)
        )
        or 0
    )


def moderation_event_to_out(event: ModerationEvent) -> chat_moderation.ModerationEvent:
    """A stored event as M4's wire shape (`contracts/chat/moderation.py`)."""
    return chat_moderation.ModerationEvent(
        id=event.id,
        message_id=event.message_id,
        actor_kind=chat_moderation.ModerationActorKind(event.actor_kind),
        actor_id=event.actor_id,
        label=chat_moderation.ModerationLabel(event.label),
        action=chat_moderation.ModerationAction(event.action),
        confidence=event.confidence,
        rationale=event.rationale,
        note=event.note,
        notice_text=event.notice_text,
        policy_version=event.policy_version,
        model_version=event.model_version,
        degraded=event.degraded,
        created_at=event.created_at,
    )


def list_moderation_queue(
    session: Session, *, after_message_id: uuid.UUID | None = None, limit: int = 25
) -> list[tuple[Message, User, ModerationEvent, int]]:
    """The review queue: `held`, not-deleted messages that have at least one
    event, each with its author, its LATEST event (the one that put it here)
    and how many events it has. Oldest first, by message id (a UUIDv7, so
    creation order), keyset-paged: `after_message_id` is the last id the
    reader saw. The queue changes under the reader as moderators work it, so
    an offset would skip or repeat an item when one is released between pages;
    "id greater than the last seen" cannot.

    A held message nobody has ruled on has no event to show and is left out:
    the wire shape's `latest_event` is required."""
    ranked = select(
        ModerationEvent.id.label("event_id"),
        ModerationEvent.message_id.label("message_id"),
        func.row_number()
        .over(
            partition_by=ModerationEvent.message_id,
            order_by=(ModerationEvent.created_at.desc(), ModerationEvent.id.desc()),
        )
        .label("rn"),
        func.count().over(partition_by=ModerationEvent.message_id).label("n"),
    ).subquery()
    stmt = (
        select(Message, User, ModerationEvent, ranked.c.n)
        .join(ranked, (ranked.c.message_id == Message.id) & (ranked.c.rn == 1))
        .join(ModerationEvent, ModerationEvent.id == ranked.c.event_id)
        .join(User, User.id == Message.author_id)
        .where(Message.status == "held", Message.deleted_at.is_(None))
    )
    if after_message_id is not None:
        stmt = stmt.where(Message.id > after_message_id)
    rows = session.execute(stmt.order_by(Message.id).limit(limit)).all()
    return [(row[0], row[1], row[2], row[3]) for row in rows]
