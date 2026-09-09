"""Connection helper + schema bootstrap for the spike.

No migration runner/tooling here on purpose -- this is exploratory code,
not production, and CREATE TABLE IF NOT EXISTS run at startup is honest
about that. A real backbone would need a real migration tool; that's a
cost worth naming in the ADR, not solving here.
"""

import glob
import os

import psycopg

# NOTE: an asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
# call used to live here, to work around psycopg refusing Windows'
# ProactorEventLoop. It was removed because it does nothing: modern
# uvicorn resolves its loop via get_loop_factory() and passes it to
# asyncio.run(..., loop_factory=...), which bypasses the global policy
# entirely -- loop_factory.py's docstring documents that dead end and
# carries the fix that actually works. Leaving the call here implied a
# second, working mechanism that does not exist.

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
