"""Week 6 Step 1: the audio retention sweeper.

Before this file, "30-day retention" was a sentence in a plan document
(docs/retro/month-1.md's Week 6 row) — infra/backups/README.md's 30-day
window is for BACKUPS, a separate, unrelated setting for a different
thing, and nothing enforced anything for the actual uploaded voice notes.
This is the first place that window is real, running code: past it, an
artifact's bytes AND its media_objects row are both actually deleted, and
the deletion is logged.

What happens to a message whose audio has been swept: GET /media/{id}
starts returning 404, identically to an id that never existed (app/
media.py's existing "unknown reference" path — tests/test_media_routes.py's
test_fetch_unknown_reference_404s_cleanly already covers this exact
response shape). That's a direct consequence of deleting the row, not a
separate choice: once the row is gone there is nothing left server-side to
tell "swept" apart from "never existed," so the two cases can't return
different responses. A distinguishable "this recording has expired"
response would need a tombstone row instead of a real delete, which is a
different design than "the row and the bytes are actually deleted" — see
OPEN_QUESTIONS.md. messages.original_media_ref is unaffected either way
(MediaObject still isn't wired to messages — see its own docstring); the
audio degrading to "player says the recording is gone" rather than
silently breaking is a client-side rendering decision for whoever wires
GET /media/{id}'s 404 into the elder UI (clients/elder-app/, out of scope
this week) — this file's job stops at making that 404 the accurate, only
answer for a swept id.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.repository import delete_media_object, find_expired_media
from app.media_storage import get_media_storage

logger = logging.getLogger(__name__)


def sweep_expired_media(*, retention_days: int, now: datetime | None = None) -> list[str]:
    """Deletes every media_objects row (and its backing bytes) older than
    `retention_days`. Returns the list of swept media ids (as strings), for
    the caller to log/assert against — tests pass an artificially aged
    artifact and a real `now` rather than waiting real days for one to
    expire.

    Bytes deleted before the row, not after: if this crashed between the
    two, an orphaned row pointing at already-gone bytes is a 500 waiting
    to happen on the next GET, whereas an orphaned FILE with no row is
    just wasted disk a future sweep run's own leftover-file cleanup (not
    built yet — see OPEN_QUESTIONS.md) could still catch. The safer
    partial-failure state is the one this ordering leaves behind."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=retention_days)
    storage = get_media_storage()

    session = SessionLocal()
    swept: list[str] = []
    try:
        for media in find_expired_media(session, older_than=cutoff):
            media_id = str(media.id)
            age_days = (now - media.created_at).days
            storage.delete(media_id)
            delete_media_object(session, media.id)
            session.commit()
            logger.info(
                "retention sweep: deleted media_id=%s author_id=%s age_days=%d "
                "(retention_days=%d)",
                media_id,
                media.author_id,
                age_days,
                retention_days,
            )
            swept.append(media_id)
    finally:
        session.close()
    return swept


async def run_retention_sweep_loop(stop_event: asyncio.Event) -> None:
    """Runs sweep_expired_media on a timer until `stop_event` is set. Same
    asyncio.to_thread pattern as app/jobs.py's run_worker_loop, for the
    same reason: SessionLocal is a sync sessionmaker, and this must not
    block the event loop the WS handler and every other route share."""
    settings = get_settings()
    interval = settings.MEDIA_RETENTION_SWEEP_INTERVAL_SECONDS
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(
                sweep_expired_media, retention_days=settings.MEDIA_RETENTION_DAYS
            )
        except Exception:
            logger.exception("retention sweep: unexpected error, retrying after interval")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            pass
