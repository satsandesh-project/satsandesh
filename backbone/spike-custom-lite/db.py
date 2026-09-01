"""Connection helper + schema bootstrap for the spike.

No migration runner/tooling here on purpose -- this is exploratory code,
not production, and CREATE TABLE IF NOT EXISTS run at startup is honest
about that. A real backbone would need a real migration tool; that's a
cost worth naming in the ADR, not solving here.
"""

import asyncio
import glob
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
    """Applies every migrations/*.sql in filename order. Idempotent
    (IF NOT EXISTS throughout), so calling this on every app startup --
    including every test run -- is intentional, not a hack.

    Filename order is why they're numbered: 002_circles.sql adds an index
    on spike_messages, which 001 has to have created first. Still no
    migration runner and still no applied-migrations table -- that
    remains a named cost in the ADR, not something to quietly solve here.
    """
    paths = sorted(glob.glob(os.path.join(_MIGRATIONS_DIR, "*.sql")))
    if not paths:
        raise RuntimeError(f"no migrations found in {_MIGRATIONS_DIR}")

    # Each file is executed whole, in one call. psycopg sends it via the
    # simple query protocol when there are no parameters, and Postgres
    # parses the statements itself.
    #
    # This replaces a hand-rolled `sql.split(";")` splitter that was here
    # in Week 2. That splitter was wrong: it split on semicolons inside
    # SQL *comments* too, turning the tail of a commented sentence into a
    # bogus statement. 002_circles.sql triggered it immediately (a comment
    # containing "reconciling; that's recorded ..."), failing with
    # `syntax error at or near "that"`. Rewording the comment would have
    # hidden the bug and left it for whoever wrote the next semicolon;
    # letting Postgres do the parsing removes the class of bug entirely.
    # Logged in the prompt journal.
    async with await get_connection() as conn:
        for path in paths:
            with open(path, encoding="utf-8") as f:
                await conn.execute(f.read())
        await conn.commit()
