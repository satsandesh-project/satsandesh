"""Week 6: `POST /media`, `GET /media/{id}` -- contracts/chat/media.py's
`MediaUploadOut` wire shape, backed by app/db/repository.py and
app/media_storage.py.

Idempotency: the SAME author re-POSTing the SAME bytes (a retry after a
dropped ack -- expected constantly on the elder pilot's target networks,
same reasoning as MessageIn.client_msg_id's own idempotency) must return
the SAME MediaRef and store no second copy. Keyed on a SHA-256 content
hash, not a client-supplied key: contracts/chat/README.md's `POST /media`
has no id-like parameter for a client to make one up with.

Size limit and format check (CLAUDE.md's security checklist: "file/media
uploads have an explicit size limit and type check") are both enforced
here, at the boundary, before a single byte reaches storage or the
database -- see _read_body_capped and the `format: AudioFormat` parameter
type below.

No-orphan guarantee: bytes are written to storage BEFORE the database row
is inserted, and deleted again if the insert doesn't end up being this
call's own row (either a genuine failure, re-raised after cleanup, or a
lost race against a concurrent identical upload -- app/db/repository.py's
create_media_object's own docstring). A row must never exist pointing at
bytes that don't exist -- that's the store actively lying about what it
holds. An unreferenced file with no row (the reverse) is comparatively
harmless and never happens on the validated-and-rejected path anyway,
since nothing is written to storage until after size/format validation
passes.
"""

import hashlib
import uuid

from contracts.chat.common import AudioFormat
from contracts.chat.media import MediaUploadOut
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.repository import create_media_object, find_media_object, get_media_object
from app.id import generate_uuid7
from app.media_storage import get_media_storage
from app.models import User

router = APIRouter()

# Content-Type per format for GET /media/{id} -- mirrors
# contracts/chat/mock/app.py's identical table. Not imported from there:
# contracts/chat/ is a dependency of services/gateway/, not the other way
# around, and the mock is a contracts/chat/ concern regardless (see
# contracts/chat/DECISIONS.md #5's no-cross-package-import rule -- same
# "don't reach sideways across an ownership boundary" spirit, even though
# that rule was written for contracts/ai/ specifically).
_CONTENT_TYPE_BY_FORMAT: dict[AudioFormat, str] = {
    AudioFormat.WEBM_OPUS: "audio/webm",
    AudioFormat.OGG_OPUS: "audio/ogg",
    AudioFormat.WAV_PCM16: "audio/wav",
    AudioFormat.MP3: "audio/mpeg",
}


async def _read_body_capped(request: Request, max_bytes: int) -> bytes:
    """Reads the request body in chunks, aborting the instant max_bytes is
    exceeded -- never buffers an oversized body fully into memory (a
    denial-of-service surface a naive `await request.body()` wouldn't
    guard against), and never hands a partially-read blob to storage: the
    caller only ever sees this return once the WHOLE body is confirmed to
    fit under the limit."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds the {max_bytes}-byte limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _to_media_upload_out(media) -> MediaUploadOut:
    return MediaUploadOut(
        uri=f"media:{media.id}",
        format=AudioFormat(media.format),
        duration_ms=media.duration_ms,
    )


@router.post("/media", response_model=MediaUploadOut)
async def upload_media(
    request: Request,
    format: AudioFormat,
    duration_ms: int | None = Query(default=None, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MediaUploadOut:
    settings = get_settings()
    data = await _read_body_capped(request, settings.MEDIA_MAX_UPLOAD_BYTES)
    if not data:
        raise HTTPException(status_code=422, detail="empty upload")

    author_id = uuid.UUID(user.id)
    sha256_hex = hashlib.sha256(data).hexdigest()

    # Fast path: this exact author already stored these exact bytes --
    # skip the storage write entirely rather than writing-then-discarding
    # on every retry (see module docstring). The overwhelmingly common
    # case under the weak-network retries this feature exists to handle
    # well.
    existing = find_media_object(db, author_id=author_id, sha256_hex=sha256_hex)
    if existing is not None:
        return _to_media_upload_out(existing)

    storage = get_media_storage()
    media_id = generate_uuid7()
    storage.put(str(media_id), data)
    try:
        media, created = create_media_object(
            db,
            media_id=media_id,
            author_id=author_id,
            format=format.value,
            sha256_hex=sha256_hex,
            size_bytes=len(data),
            duration_ms=duration_ms,
        )
    except Exception:
        # Any failure past the write -- never leave the file we just wrote
        # with nothing in the database pointing at it. (A row is worse
        # than a leaked file: it would actively claim bytes exist at a URI
        # that isn't backed by a committed row. A leaked file with no
        # claim about it is comparatively harmless.) Re-raised unchanged;
        # this is cleanup, not error handling.
        storage.delete(str(media_id))
        raise
    if not created:
        # Lost a genuine concurrent race to an identical upload from the
        # same author -- our own file is redundant, the winner's is what
        # the returned row actually points at.
        storage.delete(str(media_id))
    db.commit()
    return _to_media_upload_out(media)


@router.get("/media/{media_id}")
def fetch_media(
    media_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    try:
        media_uuid = uuid.UUID(media_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="media not found") from None

    media = get_media_object(db, media_uuid)
    if media is None:
        raise HTTPException(status_code=404, detail="media not found")

    storage = get_media_storage()
    data = storage.get(str(media.id))
    if data is None:
        # The row exists but the bytes don't -- exactly the "store lying
        # about what it holds" failure mode this feature is built to
        # avoid on the write path. Surfaced loudly here (500, not a quiet
        # 404) rather than looking like "never uploaded", which it isn't.
        raise HTTPException(status_code=500, detail="media row exists but its bytes are missing")
    return Response(
        content=data,
        media_type=_CONTENT_TYPE_BY_FORMAT[AudioFormat(media.format)],
    )
