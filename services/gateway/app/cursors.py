"""Opaque keyset-paging cursors over a UUIDv7 id.

A list that changes under the reader (a moderation queue being worked, an audit
log being appended to) cannot be paged by offset without skipping or repeating
rows, so routes page by "id after/before the last one you saw". The cursor is
that id, base64url-encoded so a client treats it as opaque. Shared by
app/moderation.py and app/admin_org.py.
"""

import base64
import binascii
import uuid

from fastapi import HTTPException


def encode_cursor(row_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(str(row_id).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> uuid.UUID:
    """The id inside a cursor, or 422 for anything this module did not make."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return uuid.UUID(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="cursor is not a valid cursor") from None
