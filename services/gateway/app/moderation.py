"""The moderator console's routes (`contracts/chat/moderation.py`, M4's,
issue #65): the review queue, one message's event trail, and release / block.

Everything that contract says "the gateway enforces" is enforced here:

- only a SIGNED token (app/tokens.py) identifies the caller, in every AUTH_MODE: the legacy
  "a bare UUID is that user" identity is refused here (401), because with it anyone who knows
  a moderator's id could read the queue and release or block (OPEN_QUESTIONS #23). Caddy cannot
  know the gateway's mode, so the refusal lives here and does not depend on `AUTH_MODE=jwt`;
- only a moderator or admin may use any of it -- by the DATABASE role
  (`users.role`). The token stub (app/auth.py) derives `role='elder'` for
  every token, so `require_role("moderator")` could never pass; the row is
  the authoritative record (the same choice app/db/repository.py's
  `user_can_fetch_media` makes);
- `expected_event_id` is an optimistic-concurrency guard: two moderators can
  open the same item, and the second one's click must not silently overwrite
  a decision they never saw (409);
- the trail is append-only: a release, a block and a later reversal are each
  a NEW event, never an edit (the table's trigger would refuse the
  alternative anyway);
- releasing a message actually delivers it. Held messages were rendered while
  still pending (stored, hidden until out), so a release is a status flip --
  but a flip alone sends nothing; `message.new` still has to go out, and a
  push to anyone offline.

Not here: appeals (Week 9) and any way to tell the SENDER why (no wire surface
for a notice exists yet -- OPEN_QUESTIONS #18), which is why `notice_sent` is
honestly False.
"""

from __future__ import annotations

import base64
import binascii
import uuid

from contracts.chat.common import MediaRef, MessageStatus
from contracts.chat.envelope import FrameType
from contracts.chat.messages import MessageStatusOut
from contracts.chat.moderation import (
    ModerationEventsOut,
    ModerationQueueItem,
    ModerationQueueOut,
    ModerationReviewIn,
    ModerationReviewOut,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.auth import bearer, get_current_user
from app.db.base import get_db
from app.db.models import User as DbUser
from app.db.moderation import (
    latest_moderation_event,
    list_moderation_events,
    list_moderation_queue,
    moderation_event_to_out,
    record_moderation_event,
)
from app.db.renderings import renderings_for_wire
from app.db.repository import get_message_by_id, set_message_status
from app.messages import _fan_out_recipients, _parse_uuid, message_to_out
from app.models import User
from app.push import maybe_push_for_message
from app.tokens import looks_like_jwt

router = APIRouter(prefix="/moderation", tags=["moderation"])


def _signed_token_only(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
    """Runs BEFORE get_current_user (declared first below), so a refused legacy token never
    reaches the stub that would provision a users row for it. No header at all is left to
    get_current_user, which answers 401 "Not authenticated"."""
    if creds is not None and not looks_like_jwt(creds.credentials):
        raise HTTPException(status_code=401, detail="Moderation requires a signed token")


def require_moderator(
    _signed: None = Depends(_signed_token_only),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DbUser:
    try:
        row = db.get(DbUser, uuid.UUID(user.id))
    except ValueError:  # a non-UUID stub token has no row to be a moderator
        row = None
    if row is None or row.role not in ("moderator", "admin"):
        raise HTTPException(status_code=403, detail="Insufficient role")
    return row


def _encode_cursor(message_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(str(message_id).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> uuid.UUID:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return uuid.UUID(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="cursor is not a valid cursor") from None


@router.get("/queue", response_model=ModerationQueueOut)
def get_queue(
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    _moderator: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> ModerationQueueOut:
    after = _decode_cursor(cursor) if cursor is not None else None
    # One extra row tells us whether another page exists, without a COUNT.
    rows = list_moderation_queue(db, after_message_id=after, limit=limit + 1)
    page = rows[:limit]
    items = [
        ModerationQueueItem(
            message_id=message.id,
            author_id=message.author_id,
            author_display_name=author.name,
            target_type=message.target_type,
            target_id=message.target_circle_id
            if message.target_type == "circle"
            else message.target_user_id,
            # What the SENDER typed: null for a voice note. What a machine HEARD is `transcript`
            # below (M4's contract 0.6.0 keeps the two apart on purpose, so ASR errors stay
            # visible to the moderator at the moment they matter).
            original_text=message.text,
            transcript=message.transcript,
            transcript_language=message.transcript_language,
            original_language=(
                message.transcript_language if message.kind == "voice" else message.source_lang
            ),
            original_media_ref=(
                MediaRef(
                    uri=message.original_media_ref,
                    format=message.media_format,
                    duration_ms=message.media_duration_ms,
                )
                if message.media_object_id is not None
                else None
            ),
            pivot_text_en=message.pivot_text_en,
            latest_event=moderation_event_to_out(latest),
            event_count=count,
            created_at=message.created_at,
        )
        for message, author, latest, count in page
    ]
    next_cursor = _encode_cursor(page[-1][0].id) if len(rows) > limit else None
    return ModerationQueueOut(items=items, next_cursor=next_cursor)


@router.get("/messages/{message_id}/events", response_model=ModerationEventsOut)
def get_events(
    message_id: str,
    _moderator: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> ModerationEventsOut:
    message_uuid = _parse_uuid(message_id, field="message_id")
    if get_message_by_id(db, message_uuid) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    events = list_moderation_events(db, message_uuid)
    return ModerationEventsOut(
        message_id=message_uuid, events=[moderation_event_to_out(e) for e in events]
    )


@router.post("/messages/{message_id}/release", response_model=ModerationReviewOut)
async def release(
    message_id: str,
    body: ModerationReviewIn,
    moderator: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> ModerationReviewOut:
    return await _decide(db, moderator, message_id, body, action="ALLOW")


@router.post("/messages/{message_id}/block", response_model=ModerationReviewOut)
async def block(
    message_id: str,
    body: ModerationReviewIn,
    moderator: DbUser = Depends(require_moderator),
    db: Session = Depends(get_db),
) -> ModerationReviewOut:
    return await _decide(db, moderator, message_id, body, action="BLOCK")


async def _decide(
    db: Session,
    moderator: DbUser,
    message_id: str,
    body: ModerationReviewIn,
    *,
    action: str,
) -> ModerationReviewOut:
    message_uuid = _parse_uuid(message_id, field="message_id")
    message = get_message_by_id(db, message_uuid)
    if message is None or message.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")

    # A release may reverse an earlier block; a block applies only to a
    # message still waiting for review.
    allowed = ("held", "blocked") if action == "ALLOW" else ("held",)
    if message.status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Message is {message.status!r}; it cannot be {action.lower()}ed",
        )
    latest = latest_moderation_event(db, message_uuid)
    if latest is None:
        raise HTTPException(status_code=409, detail="Message has no moderation history to review")
    if body.expected_event_id is not None and body.expected_event_id != latest.id:
        raise HTTPException(
            status_code=409,
            detail="The message was decided by someone else since you opened it",
        )

    label = body.label.value if body.label is not None else latest.label
    rationale = "Released by a moderator." if action == "ALLOW" else "Blocked by a moderator."
    if label != latest.label:
        rationale += f" Label corrected from {latest.label} to {label}."
    new_status = "sent" if action == "ALLOW" else "blocked"

    # The status change is the atomic claim (a WHERE on the status we read):
    # of two moderators acting at once, exactly one wins and the other gets a
    # 409 -- no event, no delivery, from the loser.
    if not set_message_status(db, message_uuid, new_status=new_status, expected=message.status):
        db.rollback()
        raise HTTPException(status_code=409, detail="The message was decided by someone else")
    event = record_moderation_event(
        db,
        message_id=message_uuid,
        actor_kind="moderator",
        actor_id=moderator.id,
        label=label,
        action=action,
        rationale=rationale,
        note=body.note,
        policy_version=latest.policy_version,
    )
    db.commit()
    db.refresh(message)

    from app.ws import manager

    if action == "ALLOW":
        frame = {
            "type": FrameType.MESSAGE_NEW.value,
            "data": message_to_out(
                message, renderings_for_wire(db, [message]).get(str(message.id))
            ).model_dump(mode="json"),
        }
        await manager.broadcast(_fan_out_recipients(db, message), frame)
        maybe_push_for_message(
            db, message=message, sender_id=message.author_id, connection_manager=manager
        )
    else:
        await manager.broadcast(
            [str(message.author_id)],
            {
                "type": FrameType.MESSAGE_STATUS.value,
                "data": MessageStatusOut(
                    id=str(message.id), status=MessageStatus.BLOCKED
                ).model_dump(mode="json"),
            },
        )

    return ModerationReviewOut(
        event=moderation_event_to_out(event),
        message_status=new_status,
        # Honest: there is no wire surface to tell the sender anything beyond
        # the status change yet (OPEN_QUESTIONS #18).
        notice_sent=False,
    )
