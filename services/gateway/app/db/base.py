"""SQLAlchemy 2.0 declarative base, plus a sync engine/session factory built
from `settings.DATABASE_URL`.

Sync, not async: `.env.example`'s `DATABASE_URL` uses the bare
`postgresql://` scheme, which SQLAlchemy resolves to its default
synchronous DBAPI (`psycopg2`) — an async engine would need an explicit
`+asyncpg` (or `+psycopg` async mode) prefix, which nothing in this repo's
committed config uses today. Reinterpreting an already-committed URL format
as implying async would be a silent, undocumented choice; this instead
matches what's actually there.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    get_settings().DATABASE_URL,
    # Bounds on how long a single query/connection attempt can hang.
    # Without these, a stalled query on a flaky network never fails --
    # it just blocks forever. That's catastrophic specifically for the
    # WS handler (app/ws.py's ws_endpoint): it's `async def` and holds
    # one synchronous Session open for its whole connection lifetime, so
    # one stuck query there freezes the entire event loop -- new
    # connections, other requests, even the healthcheck -- until the
    # process is restarted. A bounded timeout turns a silent, unrecoverable
    # hang into a fast, visible error that this request's own error
    # handling (ws.py's "any failure -> error frame, never a close" rule)
    # already knows how to report, instead of the whole process wedging.
    #
    # idle_in_transaction_session_timeout is the other half of that, and it
    # closes a failure mode statement_timeout alone cannot: a session that
    # opened a transaction and then went *quiet* is never "running a long
    # query", so no statement timeout ever fires -- it just holds its locks
    # indefinitely. That is exactly what ws_endpoint used to do (see its own
    # comment): one uncommitted `users` INSERT pinned for the whole WebSocket
    # lifetime, blocking every later write to that row. That specific leak is
    # fixed at its source, but this bounds the blast radius of any future one
    # -- Postgres itself terminates an abandoned open transaction after 30s
    # instead of letting it wedge unrelated requests until someone restarts
    # the process.
    connect_args={
        "connect_timeout": 5,
        "options": "-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000",
    },
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one Session per request, always closed
    afterward, whether or not the request handler raised."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> bool:
    """Best-effort connectivity probe for /health/ready. Returns False on
    any failure instead of letting an exception surface — a readiness
    check must report unhealthy, not crash the request that's asking
    whether this instance is healthy."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 -- must report unhealthy, not crash
        return False
