"""Week 3 Phase 7: Web Push delivery for offline/quiet-hours recipients.

`is_quiet_hours` is a pure function — no DB, no network — so app/ws.py's
push-trigger decision is independently testable from wall-clock math.
`send_push` is the only thing in this module that touches the database or
the network, and it never raises: a bad or expired subscription for one of
a user's devices must not block delivery to their other devices, matching
the per-socket try/except precedent app/ws.py's own ConnectionManager
already established for the WS fan-out case.
"""

import json
import logging
import uuid
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import (
    delete_push_subscription,
    list_member_ids_for_circle,
    list_push_subscriptions_for_user,
    upsert_push_subscription,
)
from app.models import User

if TYPE_CHECKING:
    # Type-hint only — never executes at runtime. app/ws.py already imports
    # from this module (maybe_push_for_message below), so this module
    # importing ConnectionManager back from app/ws.py at module level would
    # be circular; maybe_push_for_message takes a connection_manager
    # instance as a parameter instead, precisely to avoid needing this
    # import for anything but the annotation.
    from app.ws import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()

# Confirmed default quiet-hours window (Step 0) for a user who has set a
# timezone but never explicitly picked start/end times of their own — NOT a
# DB server_default (see app/db/models.py's User.quiet_hours_start/end
# docstring: "unset" and "explicitly set to the default" are meaningfully
# different states), so the fallback lives here, at the one place that
# actually needs to resolve "unset" into a concrete window.
DEFAULT_QUIET_HOURS_START = time(20, 0)
DEFAULT_QUIET_HOURS_END = time(7, 0)


def is_quiet_hours(
    *,
    timezone: str | None,
    quiet_hours_start: time | None,
    quiet_hours_end: time | None,
    now: datetime | None = None,
) -> bool:
    """Whether `now` (real current time if not given) falls inside the
    user's quiet-hours window, interpreted in their own local time via
    `timezone` — never pre-converted to UTC and stored, so the window
    stays correct across DST transitions the schema doc's design question
    #4 already reasons about for `timezone` itself.

    No timezone means quiet hours can't be correctly computed in this
    user's local time — never block push for a user in this state (Step 0
    answer B) rather than silently assuming UTC.
    """
    if timezone is None:
        return False

    if now is None:
        now = datetime.now(UTC)

    start = quiet_hours_start if quiet_hours_start is not None else DEFAULT_QUIET_HOURS_START
    end = quiet_hours_end if quiet_hours_end is not None else DEFAULT_QUIET_HOURS_END

    local_time = now.astimezone(ZoneInfo(timezone)).time()

    if start <= end:
        return start <= local_time < end
    # Overnight wraparound (e.g. 20:00-07:00): "inside the window" means
    # at-or-after start OR before end, not a single contiguous range a
    # plain start <= local_time <= end comparison could express.
    return local_time >= start or local_time < end


def send_push(session: Session, *, user_id: uuid.UUID, title: str, body: str) -> None:
    """Best-effort push to every device this user has subscribed. A single
    dead subscription (404/410 — the Push API's own signal that the
    endpoint is gone for good) is cleaned up and skipped; any other
    failure is logged and skipped. Either way, one bad subscription never
    stops delivery to the user's other devices."""
    settings = get_settings()
    payload = json.dumps({"title": title, "body": body})

    for subscription in list_push_subscriptions_for_user(session, user_id=user_id):
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
            )
        except WebPushException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (404, 410):
                delete_push_subscription(session, endpoint=subscription.endpoint, user_id=user_id)
            else:
                logger.warning(
                    "send_push: webpush failed for endpoint=%s status=%s",
                    subscription.endpoint,
                    status_code,
                )
        except Exception:
            # One bad subscription must not block delivery to this user's
            # other devices; see module docstring.
            logger.exception(
                "send_push: unexpected failure delivering to endpoint=%s", subscription.endpoint
            )


def maybe_push_for_message(
    session: Session,
    *,
    message: Message,
    sender_id: uuid.UUID,
    connection_manager: "ConnectionManager",
) -> None:
    """Push whoever won't see `message` delivered live over a WS socket —
    shared by both delivery paths a message can take (app/ws.py's
    `message.send` and app/messages.py's `POST /messages`), so an
    HTTP-sent message to an offline recipient triggers a push exactly the
    same way a WS-sent one does, not silently skipped because only one of
    the two call sites remembered to wire it in.

    The "real recipients" set — the other DM party, or circle members —
    always excludes `sender_id`: a sender's own other connected devices
    matter for app/ws.py's separate WS-echo fan-out, but never for push,
    regardless of which path sent the message.

    `connection_manager` is passed in, not imported, so this module never
    needs a runtime import of app/ws.py — see the TYPE_CHECKING import
    above.
    """
    if message.target_type == "circle":
        real_recipient_ids = [
            member_id
            for member_id in list_member_ids_for_circle(
                session, circle_id=message.target_circle_id
            )
            if member_id != sender_id
        ]
    else:
        real_recipient_ids = [message.target_user_id]

    sender = session.get(DbUser, sender_id)
    push_title = sender.name if sender is not None else "New message"
    push_body = message.text if message.kind == "text" and message.text else "Sent a voice message"

    for recipient_id in real_recipient_ids:
        if connection_manager.is_connected(str(recipient_id)):
            # Already (or about to be) delivered live over WS; a push on
            # top would be redundant noise.
            continue
        recipient = session.get(DbUser, recipient_id)
        if recipient is None:
            continue
        if is_quiet_hours(
            timezone=recipient.timezone,
            quiet_hours_start=recipient.quiet_hours_start,
            quiet_hours_end=recipient.quiet_hours_end,
        ):
            continue
        send_push(session, user_id=recipient_id, title=push_title, body=push_body)


# --- HTTP routes ------------------------------------------------------------
#
# Gateway-local Pydantic models, not contracts/chat/ (Step 0's confirmed
# answer to design question C): there's no second consumer of this shape
# yet, so it isn't promoted into the shared contract package. Same
# router-in-the-same-module-as-the-domain-logic pattern as app/messages.py
# and app/circles.py.


class PushSubscriptionKeysIn(BaseModel):
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeysIn


class PushUnsubscribeIn(BaseModel):
    endpoint: str


@router.post("/push/subscribe")
def post_push_subscribe(
    body: PushSubscribeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    upsert_push_subscription(
        db,
        user_id=uuid.UUID(user.id),
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/push/unsubscribe")
def post_push_unsubscribe(
    body: PushUnsubscribeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Scoped to the caller's own user_id inside delete_push_subscription —
    # a no-op, not an error, whether the endpoint never existed or belongs
    # to someone else.
    delete_push_subscription(db, endpoint=body.endpoint, user_id=uuid.UUID(user.id))
    db.commit()
    return {"status": "ok"}
