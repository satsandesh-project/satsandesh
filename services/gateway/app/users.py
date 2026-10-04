"""`GET/PATCH /me/settings` -- the signed-in user's own settings
(`contracts/chat/users.py`).

The elder app has called these since Week 5 and failed soft because they did
not exist. Why they matter now:

- `preferred_language` is what the pipeline renders INTO. The orchestrator
  (app/pipeline.py) renders only the languages the recipients prefer; until a
  user could change it, every user was Telugu and a receiver who picked Hindi in
  the app would never get a Hindi rendering.
- quiet hours (`quiet_hours_start/end` + `timezone`) have been READ by push
  (app/push.py's `is_quiet_hours`) since Month 1 with no way to be written.

Who may call it: any signed-in user, for THEMSELVES -- there is no id in the
path, so there is no way to name someone else's settings.

PATCH commits. That is not incidental: the token stub (app/auth.py) only
FLUSHES a brand-new user's row, so a request that does not commit loses it --
which is why a first GET persists nothing, and why the first PATCH must.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from zoneinfo import available_timezones

from contracts.ai.language import LanguageCode
from contracts.chat.users import UserSettingsOut, UserSettingsUpdate
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.base import get_db
from app.db.models import User as DbUser
from app.models import User

router = APIRouter(tags=["users"])

# The only fields a PATCH may touch. `contract_version` (which a client may
# send) is deliberately not among them.
_PATCHABLE = ("preferred_language", "tts_on", "quiet_hours_start", "quiet_hours_end", "timezone")


@lru_cache
def _supported_languages() -> frozenset[str]:
    # What the pipeline can render INTO (contracts/ai's closed enum). A
    # well-formed `ta` would be stored and then silently skipped by the
    # orchestrator, with no error anywhere -- so it is refused here.
    return frozenset(language.value for language in LanguageCode)


@lru_cache
def _iana_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def _row_for(user: User, db: Session) -> DbUser:
    try:
        user_id = uuid.UUID(user.id)
    except ValueError:
        # The stub's fixed fallback identity ("stub-user-1") has no users row
        # to hold settings.
        raise HTTPException(status_code=404, detail="No settings for this identity") from None
    row = db.get(DbUser, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No settings for this identity")
    return row


def _out(db: Session, row: DbUser) -> UserSettingsOut:
    if row.tts_on is None:  # a server default not yet read back from a fresh INSERT
        db.refresh(row)
    return UserSettingsOut(
        preferred_language=row.preferred_language,
        tts_on=row.tts_on,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        timezone=row.timezone,
    )


@router.get("/me/settings", response_model=UserSettingsOut)
def get_my_settings(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> UserSettingsOut:
    return _out(db, _row_for(user, db))


@router.patch("/me/settings", response_model=UserSettingsOut)
def patch_my_settings(
    update: UserSettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserSettingsOut:
    row = _row_for(user, db)
    sent = update.model_fields_set

    if "preferred_language" in sent and update.preferred_language not in _supported_languages():
        raise HTTPException(
            status_code=422,
            detail=(
                f"preferred_language {update.preferred_language!r} is not one the pipeline "
                f"can render ({sorted(_supported_languages())})"
            ),
        )
    if "timezone" in sent and update.timezone is not None and update.timezone not in _iana_zones():
        raise HTTPException(
            status_code=422,
            detail=f"timezone {update.timezone!r} is not an IANA zone name (e.g. Asia/Kolkata)",
        )

    # Omitted -> untouched; present (including an explicit null) -> applied.
    for field in _PATCHABLE:
        if field in sent:
            setattr(row, field, getattr(update, field))
    db.commit()
    db.refresh(row)
    return _out(db, row)
