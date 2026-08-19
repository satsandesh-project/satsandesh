"""Connection helper + schema bootstrap for the spike.

No migration runner/tooling here on purpose -- this is exploratory code,
not production, and CREATE TABLE IF NOT EXISTS run at startup is honest
about that. A real backbone would need a real migration tool; that's a
cost worth naming in the ADR, not solving here.
"""

import asyncio
import os
import sys

import psycopg

# psycopg's async mode refuses to run under Windows' default
# ProactorEventLoop ("Psycopg cannot use the 'ProactorEventLoop' to run in
# async mode"). This must be set before anything creates an event loop --
# db.py is imported first by every entry point (app.py, dispatcher.py,
# conftest.py), so this is the earliest common place for it. Caught on
# first real run against Postgres on this dev machine, not something
# anticipated up front -- logged in the prompt journal.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DATABASE_URL = os.environ.get("DATABASE_URL")

_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


async def get_connection() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(DATABASE_URL)


async def ensure_schema() -> None:
    """Applies migrations/001_spike_schema.sql. Idempotent (IF NOT EXISTS
    throughout), so calling this on every app startup -- including every
    test run -- is intentional, not a hack."""
    path = os.path.join(_MIGRATIONS_DIR, "001_spike_schema.sql")
    with open(path, encoding="utf-8") as f:
        sql = f.read()

    # Split on ';' rather than executing the whole file in one call: psycopg
    # only runs one statement per execute() when the simple query protocol
    # isn't in play, and mixing that up silently swallowed the 2nd/3rd
    # statement on the first pass at this (caught before it shipped --
    # logged in the prompt journal).
    statements = [s.strip() for s in sql.split(";")]
    statements = [s for s in statements if s]

    async with await get_connection() as conn:
        for statement in statements:
            await conn.execute(statement)
        await conn.commit()
