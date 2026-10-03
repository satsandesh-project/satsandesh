"""Startup recovery for messages whose delivery was scheduled in memory.

A message is created `pending` and its real delivery (app/messages.py's
`fan_out_message`) is scheduled by app/undo.py after the undo window -- in an
in-memory registry. If the gateway process restarts or dies in or after that
window, the scheduled task is simply gone and the message stays `pending`
forever: never delivered, the sender's "sent" tick never arrives. That was
the one place a voice note could still be lost after the durable job queue
(app/undo.py's own docstring names the limitation).

`recover_pending_fan_outs` runs once at startup (app/main.py's lifespan) and
re-schedules delivery for every still-`pending` message: immediately if its
undo window already elapsed, otherwise after only the time that is left.

Safe by construction, including with more than one gateway process or if it
runs twice: `fan_out_message` only acts on a `pending` message and does so
through an atomic status transition (`set_message_status`), and
`undo.schedule_fan_out` is idempotent per message id -- so a message is
delivered at most once, and an undo (DELETE /messages/{id}) that lands first
simply makes the recovered delivery a no-op.

Deliberately additive: app/undo.py itself is unchanged. Moving the undo
window onto the durable `jobs` table (so it needs no recovery at all) would
mean rewriting that module and the way DELETE /messages cancels a delivery;
this closes the lost-note failure without that refactor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app import undo
from app.db.base import SessionLocal
from app.db.repository import list_pending_messages
from app.messages import fan_out_message

logger = logging.getLogger(__name__)


async def recover_pending_fan_outs(
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """Re-schedule delivery for every `pending` message. Returns how many
    were scheduled."""
    # Lazy: app.ws imports app.messages at module level, and this module
    # is imported from app.main alongside both.
    from app.ws import manager

    def _load() -> list:
        session = session_factory()
        try:
            return list_pending_messages(session)
        finally:
            session.close()

    pending = await asyncio.to_thread(_load)
    now = datetime.now(UTC)
    for message_id, undo_expires_at in pending:
        delay = max(0.0, (undo_expires_at - now).total_seconds()) if undo_expires_at else 0.0
        undo.schedule_fan_out(
            str(message_id),
            delay,
            fan_out_message(message_id, manager, session_factory),
        )
    if pending:
        logger.info(
            "startup recovery: re-scheduled delivery of %d pending message(s)", len(pending)
        )
    return len(pending)
