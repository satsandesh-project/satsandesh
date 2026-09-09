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

DELIVERY SCHEDULING (added after an audit -- see migrations/003 and
tests/test_delivery_faults.py, which reproduce each bug before fixing it):
a row that cannot be delivered right now is pushed into the future via
next_attempt_at instead of being re-claimed on every 200ms poll. Without
that, undeliverable rows permanently filled the ORDER BY id / LIMIT batch
and starved deliverable ones.
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

# Backoff for a row that could not be delivered this cycle. Doubles per
# attempt from 1s, capped at 60s -- long enough that an offline recipient
# costs about one poll a minute instead of five a second, short enough
# that a transient blip recovers quickly on its own. The cap is what
# bounds the steady-state cost of a user who never comes back.
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0

# Consecutive send failures (socket present, send raised) before a row is
# dead-lettered. Generous on purpose: the usual cause is one stale socket,
# and dispatch_once now prunes those from the registry, so a healthy
# system should never approach this. It exists to stop an infinite hot
# loop against a genuinely un-sendable row, not to give up on people.
MAX_SEND_FAILURES = 10

# `recipient_id = ANY(%s)` is what keeps backoff invisible to real users:
# a row for someone who is connected RIGHT NOW is always claimable, no
# matter how much backoff accumulated while they were away. Only rows for
# people who are still absent stay parked in the future.
#
# This replaced a reset-on-connect approach (an UPDATE clearing backoff
# when a socket connected), which lost a race: a cycle that claimed a row
# while the user was still offline would commit its deferral *after* the
# reset landed, pushing a reconnected user's backlog back into the future.
# Measured with the crash-safety test: 200 of 300 messages stranded.
# Evaluating liveness inside the claim itself has no such window.
CLAIM_SQL = """
    SELECT id, message_id, recipient_id, attempts
    FROM spike_outbox
    WHERE status = 'pending'
      AND (next_attempt_at <= now() OR recipient_id = ANY(%s))
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT %s
"""

MARK_DELIVERED_SQL = """
    UPDATE spike_outbox SET status = 'delivered', delivered_at = now()
    WHERE id = %s
"""

# Recipient simply was not connected. Not a failure: back off so this row
# stops crowding the claim batch, but never dead-letter it -- offline
# queueing is the feature, and an elder can be offline for hours.
RESCHEDULE_OFFLINE_SQL = """
    UPDATE spike_outbox
    SET attempts = attempts + 1,
        next_attempt_at = now() + make_interval(secs => %s)
    WHERE id = %s
"""

# Recipient was connected but every send raised. That IS a fault, so it
# counts toward the dead-letter cap as well as backing off.
RESCHEDULE_SEND_FAILURE_SQL = """
    UPDATE spike_outbox
    SET attempts = attempts + 1,
        send_failures = send_failures + 1,
        status = CASE WHEN send_failures + 1 >= %s THEN 'failed' ELSE status END,
        next_attempt_at = now() + make_interval(secs => %s)
    WHERE id = %s
"""

MESSAGE_BODY_SQL = """
    SELECT body, sender_id, conversation_id FROM spike_messages WHERE id = %s
"""


def _backoff_seconds(attempts: int) -> float:
    """Exponential, capped. `attempts` is the count BEFORE this failure."""
    return min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** min(attempts, 10)))


async def claim_batch(conn: psycopg.AsyncConnection, online_ids=None, limit: int = 50):
    """The one line doing all the concurrency-safety work in this spike:
    FOR UPDATE SKIP LOCKED means a second connection running this same
    query concurrently gets whatever rows the first one *hasn't* already
    locked -- it skips past locked rows instead of blocking on them, so
    two dispatchers never end up claiming the same row.

    Row locks are held until `conn`'s transaction commits or rolls back,
    so the caller must commit (via dispatch_once) or explicitly roll back
    once done, or the rows stay locked (though never permanently -- a
    crashed connection releases its locks when Postgres notices it is gone).
    """
    async with conn.cursor() as cur:
        await cur.execute(CLAIM_SQL, (list(online_ids or []), limit))
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

    A row is marked 'delivered' only if at least one socket actually
    accepted it. Previously the send sat in a bare try/except that logged
    and then fell through to MARK_DELIVERED anyway -- so a recipient whose
    socket had died between registry.get() and the send had their message
    marked delivered and dropped, losing it permanently while reporting
    success. That directly contradicted the at-least-once guarantee above,
    which is the entire claim this spike exists to make.
    """
    online = getattr(registry, "online_user_ids", None)
    rows = await claim_batch(conn, online() if online else [], limit)
    delivered, deferred, failed = 0, 0, 0

    for outbox_id, message_id, recipient_id, attempts in rows:
        sockets = registry.get(recipient_id) if registry else set()

        if not sockets:
            async with conn.cursor() as cur:
                await cur.execute(
                    RESCHEDULE_OFFLINE_SQL, (_backoff_seconds(attempts), outbox_id)
                )
            deferred += 1
            continue

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

        any_sent = False
        for ws in list(sockets):
            try:
                await ws.send_json(payload)
                any_sent = True
            except Exception:
                # A socket that died since registry.get() above. Drop it so
                # the next cycle sees this recipient as offline (and backs
                # off) instead of retrying a corpse every poll.
                logger.warning("send failed to %s; dropping socket", recipient_id)
                remove = getattr(registry, "remove", None)
                if remove is not None:
                    remove(recipient_id, ws)

        if _DELIVERY_DELAY_SECONDS:
            await asyncio.sleep(_DELIVERY_DELAY_SECONDS)

        async with conn.cursor() as cur:
            if any_sent:
                await cur.execute(MARK_DELIVERED_SQL, (outbox_id,))
                delivered += 1
            else:
                await cur.execute(
                    RESCHEDULE_SEND_FAILURE_SQL,
                    (MAX_SEND_FAILURES, _backoff_seconds(attempts), outbox_id),
                )
                failed += 1

    await conn.commit()
    return {
        "claimed": len(rows),
        "delivered": delivered,
        "deferred": deferred,
        "send_failed": failed,
    }


async def run_forever(
    get_registry,
    poll_interval: float = 0.2,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Background loop started from app.py's lifespan. Opens a fresh
    connection every cycle rather than pooling -- simpler to reason about
    for a spike; a real implementation would use a connection pool
    (see ADR cost notes).

    connect() is INSIDE the try. It used to sit outside it, so a single
    transient connect failure -- Postgres restarting, a network blip --
    propagated straight out of this loop and killed the dispatcher task
    permanently. Nothing restarted it and nothing logged it (app.py's
    create_task never retrieved the exception), so the app went on
    answering /health with "ok" while delivering nothing, forever, until
    someone restarted the process. Reproduced with one simulated blip.
    """
    while stop_event is None or not stop_event.is_set():
        try:
            conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
            try:
                await dispatch_once(conn, get_registry())
            finally:
                await conn.close()
        except Exception:
            logger.exception("dispatch cycle failed; retrying next poll")
        await asyncio.sleep(poll_interval)
