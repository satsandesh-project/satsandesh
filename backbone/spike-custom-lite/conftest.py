"""Empty-file-with-a-purpose, same as gateway/ and ai-services/: its mere
presence makes pytest add this directory to sys.path, so `from app import
app` works in tests/ regardless of which directory pytest is invoked from.

Also holds the one fixture every test needs: a clean pair of spike tables.
These tests run against the real dev Postgres (the same one Week 1's
stack uses), not a throwaway test DB -- keeping that database honest is
part of the point of a spike. `spike_clean_db` truncates before each test
so tests don't see each other's rows.
"""

import asyncio

import psycopg
import pytest

from db import DATABASE_URL, ensure_schema


@pytest.fixture
def spike_clean_db():
    async def _clean():
        await ensure_schema()
        async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
            await conn.execute(
                "TRUNCATE spike_outbox, spike_messages RESTART IDENTITY CASCADE"
            )
            await conn.commit()

    asyncio.run(_clean())
    yield
