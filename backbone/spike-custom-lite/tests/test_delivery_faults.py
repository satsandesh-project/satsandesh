"""Regression tests for four delivery bugs found in an audit of the Week
2/3 dispatcher. Every test here failed against the code as it stood before
migrations/003 and the accompanying dispatcher fixes -- each was written
by reproducing the bug first, then fixing it, so none of them is a test
that would pass either way.

These sit apart from test_delivery.py deliberately: that file proves the
five behaviours the spike set out to demonstrate (the happy paths). This
file proves the faults those behaviours quietly assumed away.
"""

import asyncio

import psycopg
import pytest
from dispatcher import dispatch_once

from db import DATABASE_URL


class Registry:
    """Minimal stand-in for ConnectionRegistry, including remove() so the
    stale-socket pruning path is exercised the same way it is in app.py."""

    def __init__(self, online=None):
        self._online = {k: set(v) for k, v in (online or {}).items()}

    def get(self, user_id):
        return self._online.get(user_id, set())

    def remove(self, user_id, ws):
        socks = self._online.get(user_id)
        if socks:
            socks.discard(ws)
            if not socks:
                del self._online[user_id]

    def online_user_ids(self):
        return [uid for uid, socks in self._online.items() if socks]


class Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class DeadSocket:
    """A socket that died between registry.get() and the send -- the exact
    race that used to lose messages silently."""

    async def send_json(self, payload):
        raise RuntimeError("WebSocket is closed")


async def _seed(recipients, body="hi", conversation="c1"):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO spike_messages (conversation_id, sender_id, body) "
                "VALUES (%s, %s, %s) RETURNING id",
                (conversation, "alice", body),
            )
            (message_id,) = await cur.fetchone()
            for recipient in recipients:
                await cur.execute(
                    "INSERT INTO spike_outbox (message_id, recipient_id) VALUES (%s, %s)",
                    (message_id, recipient),
                )
        await conn.commit()
    return message_id


async def _rows(where, params=()):
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT status, attempts, send_failures FROM spike_outbox WHERE {where}",
                params,
            )
            return await cur.fetchall()


async def _cycle(registry, limit=50):
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        return await dispatch_once(conn, registry, limit)
    finally:
        await conn.close()


def test_offline_backlog_does_not_starve_an_online_recipient(spike_clean_db):
    """A user who never reconnects must not block delivery to everyone else.

    The claim query is `ORDER BY id ... LIMIT n`, and an undeliverable row
    used to stay `pending` and immediately due, so it was re-claimed every
    single cycle. Enough of them filled the batch permanently and messages
    for users who WERE online were never reached at all. Measured before
    the fix: 60 rows for an offline user starved one online user's message
    across five full cycles (delivered: 0).

    Uses limit=5 against 10 backlog rows so the starvation is structural
    rather than dependent on the production batch size.
    """

    async def scenario():
        for i in range(10):
            await _seed(["ghost"], body=f"ghost-{i}")
        await _seed(["alice"], body="urgent for alice")

        socket = Socket()
        registry = Registry({"alice": [socket]})
        for _ in range(3):
            await _cycle(registry, limit=5)
        return socket

    socket = asyncio.run(scenario())
    assert [m["body"] for m in socket.sent] == ["urgent for alice"], (
        "an online recipient was starved by an offline user's backlog"
    )


def test_failed_send_is_not_marked_delivered(spike_clean_db):
    """The headline claim of this spike is at-least-once delivery: a
    message may arrive twice, but is never lost. A send that raised used
    to be logged and then fall through to MARK_DELIVERED anyway, so the
    row was marked delivered, the recipient never got it, and
    dispatch_once reported `delivered: 1` while doing it.
    """

    async def scenario():
        await _seed(["bob"])
        result = await _cycle(Registry({"bob": [DeadSocket()]}))
        return result, await _rows("recipient_id = %s", ("bob",))

    result, rows = asyncio.run(scenario())
    status, _attempts, send_failures = rows[0]

    assert status == "pending", "a message nobody received was marked delivered"
    assert send_failures == 1
    assert result["delivered"] == 0
    assert result["send_failed"] == 1


def test_a_dead_socket_is_dropped_so_the_next_cycle_sees_the_user_offline(spike_clean_db):
    """Pruning is what stops a corpse being retried every poll forever --
    and it is why MAX_SEND_FAILURES is a safety valve rather than a
    countdown a real user would ever reach."""

    async def scenario():
        await _seed(["bob"])
        registry = Registry({"bob": [DeadSocket()]})
        first = await _cycle(registry)
        second = await _cycle(registry)
        return first, second, registry

    first, second, registry = asyncio.run(scenario())
    assert first["send_failed"] == 1
    assert registry.get("bob") == set(), "the dead socket was left in the registry"
    # Second cycle: the row is backed off, so nothing is even claimed --
    # and the user now reads as offline rather than as a repeat failure.
    assert second["send_failed"] == 0


def test_a_connected_recipient_is_claimable_despite_accumulated_backoff(spike_clean_db):
    """Backoff must never become a delay a real user feels.

    The claim query treats anyone currently in the registry as due, so a
    reconnecting user's whole backlog is claimable the moment they are
    connected -- however long they were away, and with no reset UPDATE to
    race against.

    An earlier version of this fix DID use a reset-on-connect UPDATE, and
    it lost that race: a cycle that claimed a row while the user was still
    offline committed its deferral after the reset landed, re-parking the
    backlog in the future. The crash-safety test caught it -- 200 of 300
    messages stranded -- which is why liveness is evaluated inside the
    claim instead.
    """

    async def scenario():
        await _seed(["carol"], body="queued while away")
        # Two cycles with carol offline push next_attempt_at well ahead.
        await _cycle(Registry())
        await _cycle(Registry())
        parked = await _rows("recipient_id = %s AND next_attempt_at > now()", ("carol",))

        # No reset, no waiting: simply being connected makes it claimable.
        socket = Socket()
        await _cycle(Registry({"carol": [socket]}))
        return parked, socket

    parked, socket = asyncio.run(scenario())
    assert parked, "an undelivered row was left immediately due, so it never backs off"
    assert [m["body"] for m in socket.sent] == ["queued while away"], (
        "a connected recipient was made to wait out backoff accrued while offline"
    )


def test_dispatcher_survives_a_transient_connection_failure(spike_clean_db):
    """One blip used to kill delivery permanently and silently.

    connect() sat outside run_forever's try, so an OperationalError
    propagated out of the loop; asyncio.create_task never retrieves an
    exception unless asked, so nothing logged it, nothing restarted it,
    and /health kept answering "ok" while the app delivered nothing until
    someone noticed and restarted the process.
    """
    from dispatcher import run_forever

    async def scenario():
        real_connect = psycopg.AsyncConnection.connect
        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise psycopg.OperationalError("simulated blip")
            return await real_connect(*args, **kwargs)

        psycopg.AsyncConnection.connect = flaky
        stop = asyncio.Event()
        try:
            task = asyncio.create_task(
                run_forever(Registry, poll_interval=0.01, stop_event=stop)
            )
            await asyncio.sleep(0.3)
            survived = not task.done()
            stop.set()
            await asyncio.wait_for(task, timeout=2)
            return survived, calls["n"]
        finally:
            psycopg.AsyncConnection.connect = real_connect

    survived, attempts = asyncio.run(scenario())
    assert attempts > 2, "the loop stopped trying after the blip"
    assert survived, "one transient connect failure killed the dispatcher permanently"


@pytest.mark.parametrize(
    "bad_id", ["abc", "", "1; DROP TABLE spike_circles", "1.5"]
)
def test_non_numeric_circle_id_is_a_clean_error_not_a_crash(spike_clean_db, bad_id):
    """These used to reach a bare int() deep inside a query call and
    surface as an unhandled ValueError -- a 500 on a malformed request."""
    from circles import OutboxCircleStore

    store = OutboxCircleStore()
    with pytest.raises(ValueError, match="circle_id must be numeric"):
        asyncio.run(store.add_member(bad_id, "u1"))


def test_adding_a_member_to_a_missing_circle_reports_not_found(spike_clean_db):
    """ON CONFLICT DO NOTHING covers a duplicate member, not a missing
    circle -- that hits the FK instead, and used to escape as a raw
    psycopg error rather than "no such circle"."""
    from circles import OutboxCircleStore

    store = OutboxCircleStore()
    with pytest.raises(LookupError, match="no such circle"):
        asyncio.run(store.add_member("999999", "u1"))
