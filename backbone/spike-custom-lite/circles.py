"""Circles + announcements on top of the Week 2 outbox.

Implements `backbone/interfaces.py`'s CircleBackbone. The important
property of this file is how little it does: an announcement is exactly
the "one message, many recipients" fan-out the Week 2 spike already
implements and already tests (see tests/test_delivery.py's
two-recipient case). `post_announcement` resolves membership into a
recipient list and then performs the *same* transactional write
`app.py`'s /send does. Delivery -- the dispatcher, SKIP LOCKED claiming,
offline queueing, ordering, crash recovery -- is untouched and
unduplicated. Circles were cheap precisely because none of that was
rebuilt.
"""

import os
import sys

import psycopg

# Spike-grade import shim. In the Docker image, interfaces.py is copied
# next to this file, so `from interfaces import ...` resolves normally.
# Running from the repo it lives one level up in backbone/, so add that.
# The real fix is a small shared installable package rather than a path
# poke -- named as a cost in the ADR, not solved here.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interfaces import CircleBackbone, CircleMessage  # noqa: E402

from db import DATABASE_URL  # noqa: E402


class OutboxCircleStore(CircleBackbone):
    """Postgres-backed circles, fanning out through the existing outbox."""

    def __init__(self, database_url: str = None):
        self._database_url = database_url or DATABASE_URL

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(self._database_url)

    async def create_circle(self, name: str) -> str:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO spike_circles (name) VALUES (%s) RETURNING id",
                    (name,),
                )
                (circle_id,) = await cur.fetchone()
            await conn.commit()
        return str(circle_id)

    async def add_member(self, circle_id: str, user_id: str) -> None:
        async with await self._connect() as conn:
            # ON CONFLICT DO NOTHING against the composite PK: idempotent
            # without a check-then-insert race, per the interface contract.
            await conn.execute(
                "INSERT INTO spike_circle_members (circle_id, user_id) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (int(circle_id), user_id),
            )
            await conn.commit()

    async def remove_member(self, circle_id: str, user_id: str) -> None:
        async with await self._connect() as conn:
            # Deletes membership only. Any spike_outbox rows already
            # written for this user stay pending and still get delivered
            # -- see the interface's post_announcement contract on why
            # that's deliberate.
            await conn.execute(
                "DELETE FROM spike_circle_members WHERE circle_id = %s AND user_id = %s",
                (int(circle_id), user_id),
            )
            await conn.commit()

    async def list_members(self, circle_id: str) -> list:
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id FROM spike_circle_members "
                    "WHERE circle_id = %s ORDER BY user_id",
                    (int(circle_id),),
                )
                rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def post_announcement(self, circle_id: str, sender_id: str, body: str) -> str:
        """Membership resolution and the fan-out write happen in ONE
        transaction. Doing the SELECT outside it would leave a window
        where a member added mid-post gets silently skipped while
        appearing to have been present -- a race that would be miserable
        to reproduce later. Inside the transaction, the recipient set is
        whatever membership was at that instant, consistently."""
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id FROM spike_circle_members WHERE circle_id = %s",
                    (int(circle_id),),
                )
                recipients = [r[0] for r in await cur.fetchall()]

                await cur.execute(
                    "INSERT INTO spike_messages (conversation_id, sender_id, body) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (str(circle_id), sender_id, body),
                )
                (message_id,) = await cur.fetchone()

                # One outbox row per member, same transaction as the
                # message itself -- this is the Week 2 durability
                # guarantee, reused rather than reimplemented.
                for recipient_id in recipients:
                    await cur.execute(
                        "INSERT INTO spike_outbox (message_id, recipient_id) "
                        "VALUES (%s, %s)",
                        (message_id, recipient_id),
                    )
            await conn.commit()
        return str(message_id)

    async def list_messages(self, circle_id: str, limit: int = 50, before: str = None) -> list:
        sql = (
            "SELECT id, conversation_id, sender_id, body, created_at "
            "FROM spike_messages WHERE conversation_id = %s"
        )
        params = [str(circle_id)]
        if before is not None:
            sql += " AND id < %s"
            params.append(int(before))
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit)

        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()

        return [
            CircleMessage(
                id=str(r[0]),
                circle_id=r[1],
                sender_id=r[2],
                body=r[3],
                created_at=r[4],
            )
            for r in rows
        ]
