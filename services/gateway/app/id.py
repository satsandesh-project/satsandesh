"""RFC 9562 UUIDv7 generation — the one shared helper every model in
app/db/models.py uses as its `id` default (docs/DECISIONS.md: "Chat message
`id` is UUIDv7", extended to `users.id`/`circles.id` too per the Week 2
schema review's design question #2 resolution).

Implemented by hand rather than via a dependency: this repo pins
`requires-python = ">=3.11"` (pyproject.toml) and the venv here runs 3.11.5;
`uuid.uuid7()` doesn't land in the stdlib until 3.14.
"""

import os
import time
import uuid


def generate_uuid7() -> uuid.UUID:
    """A UUIDv7: a 48-bit big-endian millisecond Unix timestamp in the
    first 6 bytes, then a 4-bit version, 12 bits of random fill, a 2-bit
    variant, and 62 more bits of random fill (RFC 9562, section 5.7).
    Byte-wise comparison of two UUIDv7 values is therefore a compare of
    their generation timestamps, with the trailing random bits breaking
    ties inside the same millisecond — exactly what makes `id > since_id`
    mean "generated after" for the chat sync cursor (docs/SCHEMA_DRAFT.md,
    the "`id` as native `uuid`" section)."""
    unix_ts_ms = time.time_ns() // 1_000_000
    ts_bytes = unix_ts_ms.to_bytes(6, byteorder="big")

    tail = bytearray(os.urandom(10))
    tail[0] = (tail[0] & 0x0F) | 0x70  # version 7 in the high nibble of byte 6
    tail[2] = (tail[2] & 0x3F) | 0x80  # variant 0b10 in the high 2 bits of byte 8

    return uuid.UUID(bytes=ts_bytes + bytes(tail))
