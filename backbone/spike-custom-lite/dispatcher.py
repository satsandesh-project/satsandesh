"""Claims pending spike_outbox rows and pushes them to connected
recipients.

Runs as a background asyncio task inside app.py's own process (see
app.py's lifespan), not as a separate OS process. That's a deliberate
design choice, not an oversight: the connection registry is in-process
memory, so a genuinely separate dispatcher process couldn't reach a
recipient's live socket without a pub/sub layer bridging the two
processes -- exactly the kind of cost worth naming plainly in the ADR
rather than building around silently.

"Two dispatcher instances must not double-deliver" (behaviour 5) is
tested by running two independent claim_batch() calls concurrently
against real Postgres, each on its own connection -- that's what actually
exercises FOR UPDATE SKIP LOCKED, independent of whether the two callers
happen to live in one process or two.
"""

import asyncio
import logging
import os
from typing import Optional

import psycopg

from db import DATABASE_URL

logger = logging.getLogger("spike.dispatcher")

# Test-only fault-injection knob, defaulting to a no-op. Zero effect on
# Docker/production runs. Exists because the push-then-crash-before-commit
# duplicate window (see dispatch_once's docstring) is real but, at local
# loopback speed, only microseconds wide per message -- too narrow to hit
# reliably from an external test process without deliberately widening it.
# See test_crash_safety.py for how this gets used.
_DELIVERY_DELAY_SECONDS = float(os.environ.get("SPIKE_DELIVERY_DELAY_MS", "0")) / 1000

CLAIM_SQL = """
    SELECT id, message_id, recipient_id, attempts
    FROM spike_outbox
    WHERE status = 'pending'
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT %s
"""

MARK_DELIVERED_SQL = """
    UPDATE spike_outbox SET status = 'delivered', delivered_at = now()
    WHERE id = %s
"""

MARK_RETRY_SQL = """
    UPDATE spike_outbox SET attempts = attempts + 1 WHERE id = %s
"""

MESSAGE_BODY_SQL = """
    SELECT body, sender_id, conversation_id FROM spike_messages WHERE id = %s
"""


async def claim_batch(conn: psycopg.AsyncConnection, limit: int = 50):
    """The one line doing all the concurrency-safety work in this spike:
    FOR UPDATE SKIP LOCKED means a second connection running this same
    query concurrently gets whatever rows the first one *hasn't* already
    locked -- it skips past locked rows instead of blocking on them, so
    two dispatchers never end up claiming the same row.

    Row locks are held until `conn`'s transaction commits or rolls back,
    so the caller must commit (via dispatch_once) or explicitly roll back
    once done, or the rows stay locked (though never permanently -- a
    crashed connection releases its locks when Postgres notices it's gone).
    """
    async with conn.cursor() as cur:
        await cur.execute(CLAIM_SQL, (limit,))
        return await cur.fetchall()


async def dispatch_once(conn: psycopg.AsyncConnection, registry, limit: int = 50) -> dict:
    """One claim-and-deliver pass. Returns counts for tests/observability.

    Duplicates note (see ADR): delivery happens over the websocket *before*
    the DB commit marking the row 'delivered'. If this process dies in
    that exact window -- pushed to the socket, but before the UPDATE
    commits -- the row is still 'pending' after the crash (Postgres rolls
    back the whole uncommitted transaction) and gets redelivered next
    cycle. That's at-least-once delivery: no message is ever lost, but a
    recipient can see the same message twice. This spike does not
    deduplicate; see the ADR for what that would cost.
    """
    rows = await claim_batch(conn, limit)
    delivered, retried = 0, 0

    for outbox_id, message_id, recipient_id, _attempts in rows:
        sockets = registry.get(recipient_id) if registry else set()
        if sockets:
            async with conn.cursor() as cur:
                await cur.execute(MESSAGE_BODY_SQL, (message_id,))
                body, sender_id, conversation_id = await cur.fetchone()
            payload = {
                "outbox_id": outbox_id,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "sender_id": sender_id,
                "body": body,
            }
            for ws in list(sockets):
                try:
                    await ws.send_json(payload)
                except Exception:
                    logger.warning("send failed to %s", recipient_id, exc_info=True)
            if _DELIVERY_DELAY_SECONDS:
                await asyncio.sleep(_DELIVERY_DELAY_SECONDS)
            async with conn.cursor() as cur:
                await cur.execute(MARK_DELIVERED_SQL, (outbox_id,))
            delivered += 1
        else:
            async with conn.cursor() as cur:
                await cur.execute(MARK_RETRY_SQL, (outbox_id,))
            retried += 1

    await conn.commit()
    return {"claimed": len(rows), "delivered": delivered, "retried": retried}


async def run_forever(
    get_registry,
    poll_interval: float = 0.2,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Background loop started from app.py's lifespan. Opens a fresh
    connection every cycle rather than pooling -- simpler to reason about
    for a spike; a real implementation would use a connection pool
    (see ADR cost notes)."""
    while stop_event is None or not stop_event.is_set():
        conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
        try:
            await dispatch_once(conn, get_registry())
        except Exception:
            logger.exception("dispatch_once failed")
        finally:
            await conn.close()
        await asyncio.sleep(poll_interval)
