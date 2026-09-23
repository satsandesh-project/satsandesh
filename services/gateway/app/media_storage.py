"""Week 6: media storage backend.

Decision (see PR description / docs/prompt-journal.md for the full
reasoning): a mounted local-disk volume, not an S3-compatible service
(MinIO) in docker-compose. Estimated disk cost for this pilot's actual
scale -- ~100KB per 30-second Opus voice note, 15-30 elders, 30 days --
lands under ~2GB even at generous usage assumptions, well within what a
single volume on the shared host can hold. That's the reason MinIO's real
benefit (scaling past one host's disk) isn't a problem this pilot has yet;
adding a second container, its credentials, and a second thing to back up
buys safety margin this scale doesn't need.

ONE small interface (MediaStorage) so a different backend can be swapped
in later without touching app/media.py's routes or
app/db/repository.py's create_media_object at all -- only get_media_storage
below, and whichever class it constructs, would change.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class MediaStorage(Protocol):
    """Every method takes/returns raw bytes keyed by an opaque media_id
    (always a server-generated UUID7 string -- see app/db/models.py's
    MediaObject -- never anything client-supplied), so no implementation
    of this interface ever needs to sanitize a caller-provided path."""

    def put(self, media_id: str, data: bytes) -> None: ...
    def get(self, media_id: str) -> bytes | None: ...
    def delete(self, media_id: str) -> None: ...


class LocalDiskMediaStorage:
    """Stores each object as `<root>/<media_id>.bin`. `media_id` is always
    a server-generated UUID7, never a client-supplied string (see
    app/id.py's generate_uuid7 and app/media.py's call site) -- so there is
    no path-traversal surface here; nothing derived from request input
    ever reaches a filesystem path."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, media_id: str) -> Path:
        return self._root / f"{media_id}.bin"

    def put(self, media_id: str, data: bytes) -> None:
        # Write to a temp file in the same directory, then rename -- an
        # os.replace on the same filesystem is atomic, so a concurrent
        # reader can never observe a partially-written file at the real
        # path, even under a crash mid-write. This is on top of, not
        # instead of, app/media.py's own upfront size check: that check
        # stops an oversized upload before it ever reaches this method at
        # all; this guards the write itself for whatever does get here.
        tmp_path = self._path(media_id).with_suffix(".tmp")
        tmp_path.write_bytes(data)
        os.replace(tmp_path, self._path(media_id))

    def get(self, media_id: str) -> bytes | None:
        path = self._path(media_id)
        if not path.exists():
            return None
        return path.read_bytes()

    def delete(self, media_id: str) -> None:
        # missing_ok=True: app/media.py calls this to clean up after a
        # race it lost (app/db/repository.py's create_media_object
        # returning created=False) -- the file it's cleaning up definitely
        # exists in that case, but a delete-what-may-already-be-gone
        # method is also the right building block for any future cleanup
        # job (Week 12-style), which shouldn't error on a file someone
        # else already removed.
        self._path(media_id).unlink(missing_ok=True)


@lru_cache
def get_media_storage() -> MediaStorage:
    return LocalDiskMediaStorage(get_settings().MEDIA_STORAGE_ROOT)
