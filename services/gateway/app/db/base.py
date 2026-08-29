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

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(get_settings().DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one Session per request, always closed
    afterward, whether or not the request handler raised."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
