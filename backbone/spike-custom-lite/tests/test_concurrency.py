"""Behaviour 5: two dispatcher instances must not double-deliver.

Tests the actual mechanism (FOR UPDATE SKIP LOCKED) directly rather than
through two full app processes -- what matters is two independent
Postgres transactions racing to claim the same rows, which is exactly as
real with two connections in one process as with two processes. Running
it this way keeps the test fast and free of subprocess/port-binding
flakiness (see test_crash_safety.py for where that complexity actually
earns its keep).
"""

import asyncio

import psycopg
from dispatcher import claim_batch

from db import DATABASE_URL


async def _seed(n: int) -> None:
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO spike_messages (conversation_id, sender_id, body) "
                "VALUES (%s, %s, %s) RETURNING id",
                ("c1", "alice", "concurrency-test"),
            )
            row = await cur.fetchone()
            message_id = row[0]
            for i in range(n):
                await cur.execute(
                    "INSERT INTO spike_outbox (message_id, recipient_id) VALUES (%s, %s)",
                    (message_id, f"recipient-{i}"),
                )
        await conn.commit()


async def _claim_and_mark_delivered(limit: int):
    """Simulates one dispatcher instance: claim, hold the transaction open
    for a moment (maximizing the chance a genuinely concurrent second
    claimer would collide if SKIP LOCKED weren't doing its job), then
    commit."""
    conn = await psycopg.AsyncConnection.connect(DATABASE_URL)
    try:
        rows = await claim_batch(conn, limit=limit)
        await asyncio.sleep(0.3)
        ids = [r[0] for r in rows]
        for outbox_id in ids:
            await conn.execute(
                "UPDATE spike_outbox SET status='delivered', delivered_at=now() WHERE id=%s",
                (outbox_id,),
            )
        await conn.commit()
        return ids
    finally:
        await conn.close()


def test_two_concurrent_dispatchers_never_claim_the_same_row(spike_clean_db):
    n = 40
    # Cap each claimer below n: if either could grab all 40 rows in one
    # query, the other would trivially get zero and "no overlap" would
    # pass without ever really being contended for. Capping at 25 forces
    # both to end up with a genuine share (40 - 25 = 15 minimum for the
    # second claimer).
    per_claimer_limit = 25

    asyncio.run(_seed(n))

    async def _run_both():
        return await asyncio.gather(
            _claim_and_mark_delivered(per_claimer_limit),
            _claim_and_mark_delivered(per_claimer_limit),
        )

    claimed_a, claimed_b = asyncio.run(_run_both())

    overlap = set(claimed_a) & set(claimed_b)
    assert not overlap, f"double-claimed rows: {overlap}"
    assert len(claimed_a) > 0 and len(claimed_b) > 0, (
        "one claimer got everything -- the test didn't actually force "
        "contention, so it isn't proving anything"
    )
    assert set(claimed_a) | set(claimed_b) == set(range(1, n + 1))

    print(
        f"\n[concurrency] seeded={n} claimer_A={len(claimed_a)} "
        f"claimer_B={len(claimed_b)} overlap={len(overlap)}"
    )
