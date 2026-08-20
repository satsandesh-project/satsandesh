"""`POST /messages` and `GET /messages` — contracts/chat/'s messages.py and
envelope.py wire shapes, backed by app/db/repository.py.

Authorization: circle targets require the caller to be a member
(app/db/repository.py::is_circle_member) — anyone can be DMed, but nobody
can post into or read a circle they haven't joined. There's no equivalent
membership check for a DM target: the resolved conversation is always
(caller, target_id), so a caller can never address anyone else's DM through
this wire shape (see tests/test_message_routes.py's
test_get_messages_rejects_non_participant docstring).
"""

import uuid

from contracts.chat.common import TargetType
from contracts.chat.envelope import SyncBatch
from contracts.chat.messages import AckOut, MessageIn, MessageOut
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.base import get_db
from app.db.models import Message
from app.db.repository import (
    create_message,
    find_conversation_id,
    get_messages_since,
    is_circle_member,
)
from app.models import User

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


@router.post("/messages", response_model=AckOut)
def post_message(
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
            # try/except around create_message below, so that would
            # otherwise be a bare 500, not even a clean error response.
            raise HTTPException(status_code=422, detail="cannot send a DM to yourself")
        target_user_id = target_uuid

    message = create_message(
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
    )
    # create_message only flushes (SAVEPOINT-scoped) — the repository layer
    # deliberately leaves the transaction boundary to its caller. This is
    # the end of the request's unit of work, so it commits here.
    db.commit()

    return AckOut(client_msg_id=body.client_msg_id, id=str(message.id), status=message.status)


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
