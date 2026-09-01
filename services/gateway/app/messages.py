"""`POST /messages`, `GET /messages`, and `DELETE /messages/{id}` —
contracts/chat/'s messages.py and envelope.py wire shapes, backed by
app/db/repository.py.

Authorization: circle targets require the caller to be a member
(app/db/repository.py::is_circle_member) — anyone can be DMed, but nobody
can post into or read a circle they haven't joined. There's no equivalent
membership check for a DM target: the resolved conversation is always
(caller, target_id), so a caller can never address anyone else's DM through
this wire shape (see tests/test_message_routes.py's
test_get_messages_rejects_non_participant docstring).

Week 4 Phase 8: a message is created `pending` and its real delivery is
deferred `settings.UNDO_WINDOW_SECONDS` via app/undo.py's scheduler
(`fan_out_message` below is the deferred half) — `DELETE /messages/{id}`
lets the author pull it back within that window. Both this route and
app/ws.py's `message.send` handler schedule the exact same `fan_out_message`
coroutine, so an HTTP-sent and a WS-sent message go through one delivery
path, not two that could quietly drift apart.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from contracts.chat.common import MessageStatus, TargetType
from contracts.chat.envelope import FrameType, SyncBatch
from contracts.chat.messages import AckOut, MessageIn, MessageOut
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from sqlalchemy.orm import Session

from app import undo
from app.auth import get_current_user
from app.config import get_settings
from app.db.base import SessionLocal, get_db
from app.db.models import Message
from app.db.repository import (
    create_message_with_created_flag,
    find_conversation_id,
    get_message_by_id,
    get_messages_since,
    is_circle_member,
    list_member_ids_for_circle,
    set_message_status,
)
from app.models import User
from app.push import maybe_push_for_message

if TYPE_CHECKING:
    # Type-hint only — see app/push.py's identical TYPE_CHECKING import for
    # why: app/ws.py imports this module (message_to_out, fan_out_message)
    # at module level, so a module-level `from app.ws import ConnectionManager`
    # here would be circular.
    from app.ws import ConnectionManager

router = APIRouter()


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be a valid UUID") from None


def message_to_out(message: Message) -> MessageOut:
    """Maps a stored Message row to its wire shape. Each row's own real
    target, not any caller's frame of reference — for a DM, that flips
    depending on which direction a given message went (docs/SCHEMA_DRAFT.md
    design question #1a: "the other party from one side" is not the same
    value for both directions). Shared by GET /messages below, and by
    app/ws.py's `message.new` and `sync.batch` frames — one mapping, not
    three copies that could quietly drift apart."""
    return MessageOut(
        id=str(message.id),
        author_id=str(message.author_id),
        target_type=message.target_type,
        target_id=(
            str(message.target_circle_id)
            if message.target_type == "circle"
            else str(message.target_user_id)
        ),
        kind=message.kind,
        text=message.text,
        created_at=message.created_at,
        status=message.status,
    )


def _fan_out_recipients(session: Session, message: Message) -> list[str]:
    """Same recipient set app/ws.py's `_handle_message_send` computes for
    its own (now-immediate-only-for-the-echo) broadcast: circle members, or
    the other DM party plus the sender's own other devices."""
    if message.target_type == "circle":
        return [
            str(member_id)
            for member_id in list_member_ids_for_circle(session, circle_id=message.target_circle_id)
        ]
    return [str(message.target_user_id), str(message.author_id)]


async def fan_out_message(
    message_id: uuid.UUID,
    conn_manager: "ConnectionManager",
    session_factory: Callable[[], Session],
    *,
    exclude: WebSocket | None = None,
) -> None:
    """The deferred half of message delivery — app/undo.py schedules this
    to run `settings.UNDO_WINDOW_SECONDS` after a message is created,
    unless `DELETE /messages/{id}` cancels it first. Opens its own session
    via `session_factory` rather than reusing the request/connection's own:
    this can run long after either has ended.

    `exclude`: the WS route's own originating socket, so the sender's exact
    sending device doesn't get a redundant `message.new` on top of the
    `message.ack` it already got (app/ws.py's `_handle_message_send` closes
    over its own `websocket` and passes it through here) — `None` for an
    HTTP-originated send, which has no such socket to exclude, so the
    sender's connected devices (all of them, HTTP has no "the one that
    sent it" to distinguish) receive `message.new` like any other
    recipient.

    A no-op if the message is no longer `pending` by the time this runs —
    covers both the message having already been cancelled (the expected
    case when app/undo.py's cancellation didn't win the race in time, which
    shouldn't happen since cancel_fan_out is synchronous with the DB update
    below, but is checked anyway rather than assumed) and this task somehow
    running twice.
    """
    session = session_factory()
    try:
        message = get_message_by_id(session, message_id)
        if message is None or message.status != MessageStatus.PENDING.value:
            return

        updated = set_message_status(
            session,
            message_id,
            new_status=MessageStatus.SENT.value,
            expected=MessageStatus.PENDING.value,
        )
        session.commit()
        if not updated:
            # Lost a race with a concurrent DELETE /messages/{id} undo
            # between the check above and this update.
            return
        message.status = MessageStatus.SENT.value

        new_frame = {
            "type": FrameType.MESSAGE_NEW.value,
            "data": message_to_out(message).model_dump(mode="json"),
        }
        await conn_manager.broadcast(
            _fan_out_recipients(session, message), new_frame, exclude=exclude
        )

        maybe_push_for_message(
            session, message=message, sender_id=message.author_id, connection_manager=conn_manager
        )
    finally:
        session.close()


@router.post("/messages", response_model=AckOut)
async def post_message(
    body: MessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AckOut:
    target_uuid = _parse_uuid(body.target_id, field="target_id")
    caller_id = uuid.UUID(user.id)

    target_user_id: uuid.UUID | None = None
    target_circle_id: uuid.UUID | None = None
    if body.target_type is TargetType.CIRCLE:
        if not is_circle_member(db, circle_id=target_uuid, user_id=caller_id):
            raise HTTPException(status_code=403, detail="Not a member of this circle")
        target_circle_id = target_uuid
    else:
        if target_uuid == caller_id:
            # Not a supported case — see app/ws.py's identical check for
            # the full reasoning: docs/SCHEMA_DRAFT.md's `conversations`
            # table enforces a *strict* CHECK (user_a < user_b), making a
            # self-pair structurally impossible at the DB level, and it's
            # never discussed anywhere as a supported "note to self"
            # feature. Rejected explicitly here rather than left to
            # surface as an unhandled IntegrityError — this route has no
            # try/except around create_message_with_created_flag below, so
            # that would otherwise be a bare 500, not even a clean error
            # response.
            raise HTTPException(status_code=422, detail="cannot send a DM to yourself")
        target_user_id = target_uuid

    settings = get_settings()
    undo_expires_at = datetime.now(UTC) + timedelta(seconds=settings.UNDO_WINDOW_SECONDS)

    message, created = create_message_with_created_flag(
        db,
        author_id=caller_id,
        target_type=body.target_type.value,
        target_user_id=target_user_id,
        target_circle_id=target_circle_id,
        kind=body.kind.value,
        text=body.text,
        original_media_ref=body.media_ref.uri if body.media_ref is not None else None,
        media_duration_ms=body.media_ref.duration_ms if body.media_ref is not None else None,
        source_lang=body.source_lang,
        client_msg_id=body.client_msg_id,
        undo_expires_at=undo_expires_at,
    )
    # create_message_with_created_flag only flushes (SAVEPOINT-scoped) —
    # the repository layer deliberately leaves the transaction boundary to
    # its caller. This is the end of the request's unit of work, so it
    # commits here.
    db.commit()

    if created:
        # app/ws.py imports this module (message_to_out, fan_out_message) at
        # module level, so a module-level `from app.ws import manager` here
        # would be circular; this local import only runs at request time,
        # once both modules are already fully loaded.
        from app.ws import manager

        undo.schedule_fan_out(
            str(message.id),
            settings.UNDO_WINDOW_SECONDS,
            fan_out_message(message.id, manager, SessionLocal),
        )
    # A retry of an already-created send (created=False) recovers the same
    # row and returns the same ack — its fan-out was already scheduled by
    # the original call, so scheduling a second one here would either be a
    # harmless no-op (app/undo.py's own idempotency guard) or, worse, leak
    # an unawaited coroutine if constructed without ever being scheduled.

    return AckOut(client_msg_id=body.client_msg_id, id=str(message.id), status=message.status)


@router.delete("/messages/{message_id}", status_code=204)
def delete_message(
    message_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Undo, within the window: pulls back a still-`pending` message before
    its scheduled fan-out runs. 404/403 read the row's *current* fields
    directly; the actual cancel-and-transition below still goes through
    set_message_status's atomic WHERE clause, not a second trust of that
    same read, since app/undo.py's scheduled fan-out runs on a different
    thread than this (synchronous) route handler and could flip the status
    between the read and the write."""
    message_uuid = _parse_uuid(message_id, field="message_id")
    message = get_message_by_id(db, message_uuid)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.author_id != uuid.UUID(user.id):
        raise HTTPException(status_code=403, detail="Not the author of this message")
    if message.status != MessageStatus.PENDING.value:
        raise HTTPException(
            status_code=409, detail="Undo window has closed or message already cancelled"
        )

    undo.cancel_fan_out(str(message.id))
    updated = set_message_status(
        db,
        message_uuid,
        new_status=MessageStatus.CANCELLED.value,
        expected=MessageStatus.PENDING.value,
    )
    db.commit()
    if not updated:
        # Lost the race described in the docstring above: fan-out won.
        raise HTTPException(
            status_code=409, detail="Undo window has closed or message already cancelled"
        )


@router.get("/messages", response_model=SyncBatch)
def get_messages(
    target_type: TargetType,
    target_id: str,
    since_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncBatch:
    target_uuid = _parse_uuid(target_id, field="target_id")
    caller_id = uuid.UUID(user.id)

    if target_type is TargetType.CIRCLE:
        if not is_circle_member(db, circle_id=target_uuid, user_id=caller_id):
            raise HTTPException(status_code=403, detail="Not a member of this circle")
        conversation_id: uuid.UUID | None = target_uuid
    else:
        # Read-only resolution (find_conversation_id, not
        # _get_or_create_conversation): a sync of a DM that's never been
        # messaged must return empty history, not create a conversations
        # row as a side effect of a GET.
        conversation_id = find_conversation_id(db, caller_id, target_uuid)

    if conversation_id is None:
        return SyncBatch(target_type=target_type, target_id=target_id, messages=[], has_more=False)

    since_uuid = _parse_uuid(since_id, field="since_id") if since_id is not None else None
    # Fetch one extra row past the page to learn whether more remain,
    # without a second COUNT query.
    rows = get_messages_since(
        db, conversation_id=conversation_id, since_id=since_uuid, limit=limit + 1
    )
    has_more = len(rows) > limit
    page = rows[:limit]

    messages = [message_to_out(row) for row in page]
    return SyncBatch(
        target_type=target_type, target_id=target_id, messages=messages, has_more=has_more
    )
