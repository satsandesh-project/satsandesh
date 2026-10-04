"""
The signed-in user's own settings: `GET /me/settings` and `PATCH /me/settings`.

The elder app has called these since Week 5 (quiet hours) and Week 7 (content
language, speech on/off) and fails soft because the gateway had no such route.
The shapes here are what that client already sends and reads, so nothing on the
client has to change to use them.

PATCH is a partial update with one rule that matters: a field that is
**omitted** is left alone, a field that is **explicitly null** is cleared. The
client relies on that to clear a quiet-hours time the person emptied
(`quiet_hours_start: null`), so `UserSettingsUpdate` keeps the distinction
(`model_fields_set`) rather than collapsing both to `None`. Two fields cannot be
cleared -- `preferred_language` and `tts_on` are NOT NULL in the database -- so
an explicit null for either is a client bug and is refused here.

What is deliberately NOT validated here: whether `preferred_language` is one the
pipeline can render, and whether `timezone` is a real IANA name. Both need
knowledge this package does not have (DECISIONS.md #5: it does not import
`contracts/ai/`; the IANA database lives with the gateway), so the route checks
them and answers 422.
"""

from __future__ import annotations

from datetime import time

from pydantic import Field, StrictBool, model_validator

from contracts.chat.common import VersionedModel
from contracts.chat.renderings import LANGUAGE_PATTERN


class UserSettingsOut(VersionedModel):
    """Response body for `GET /me/settings` (and for a successful PATCH).

    `quiet_hours_start` / `quiet_hours_end` serialise as `"HH:MM:SS"`, or null
    when the person has not set them (push then uses its own default window).
    `timezone` is an IANA name such as `Asia/Kolkata`, or null. **Quiet hours
    are only enforced for a user who has a timezone**: with none, push cannot
    work out the person's local time and never suppresses (app/push.py's
    `is_quiet_hours`). So a window with no timezone is stored and has no effect.
    """

    preferred_language: str = Field(pattern=LANGUAGE_PATTERN)
    tts_on: bool
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    timezone: str | None = None


class UserSettingsUpdate(VersionedModel):
    """Request body for `PATCH /me/settings`. Every field is optional; see the
    module docstring for omitted-vs-null."""

    preferred_language: str | None = Field(default=None, pattern=LANGUAGE_PATTERN)
    # StrictBool: the client sends JSON true/false. A lax bool would turn the
    # string "false" into False and "yes" into True and hide a client bug.
    tts_on: StrictBool | None = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    timezone: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _the_two_required_fields_cannot_be_cleared(self) -> UserSettingsUpdate:
        for field in ("preferred_language", "tts_on"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared (omit it to leave it unchanged)")
        return self
