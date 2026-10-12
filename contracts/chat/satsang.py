"""
The scheduled satsang broadcast (`/sessions*`).

A satsang is a **one-to-many** discourse: the speaker's audio goes out over HLS, which the
proposal chose because it is "extremely robust on 3G/4G, comfortable at 48 kbps" and because
"the 10-20 s latency is irrelevant for a discourse" (proposal §7.4). Sessions are scheduled
ahead, and each user is reminded in their own language.

Three decisions worth knowing before reading the shapes:

- **A satsang is not scoped to a circle.** The proposal describes it as one-to-many to the
  community (§7.4, objective O4) and expects only a handful in a pilot ("two live satsangs",
  §13); circles are the scoping concept for *messages*. So there is no `circle_id` here, and
  the gateway allows **at most one `live` session at a time**, enforced by a partial unique
  index rather than by Python.
- **The stream key is a credential, and no read path can leak it** -- because the database
  never holds it. Only `stream_key_sha256` is stored; the key itself exists in exactly one
  payload, `SatsangSessionCreated`, returned once when a session is created or its key is
  rotated. OPEN_QUESTIONS #25 (the Week 7 read path that returned every message not yet out)
  happened because a read path returned something it held, so the fix here is for it to hold
  nothing. The cost, deliberately accepted: a lost key cannot be shown again, only rotated --
  the same shape as `AdminUserCreated.access_token`, "shown to the admin once ... can be
  re-issued".
- **The key is what authorises going live.** `scheduled -> live` is not a user action: it
  happens when a valid key publishes to the ingest server. That is why it is a credential and
  not a convenience.

Note for anyone reading the proposal alongside this: its §8 data-model sketch has a single
`sessions (type, schedule, lead_queue)` table covering satsang *and* bhajan. The Months 3-4
plan split those into separate features owned by different people, so bhajan floor control
lives in its own `rooms` / `lead_queue` tables and this module covers only the broadcast. There
is deliberately no `type` column: the distinction is which table a row is in.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import AwareDatetime, Field, field_validator, model_validator

from contracts.chat.common import VersionedModel

MAX_TITLE_LENGTH = 120

STREAM_KEY_BYTES = 32
"""Random bytes behind a stream key (`secrets.token_urlsafe`), as the `invites` table already
does for an onboarding token. 256 bits, so a plain SHA-256 is the right thing to store: this is
a high-entropy token, not a human password, and `on_publish` needs an O(1) lookup by hash."""


class SatsangStatus(str, Enum):
    """`scheduled -> live -> ended`, plus `cancelled` from `scheduled` only.

    `live -> cancelled` is not offered: once a discourse is on the air you end it, you do not
    pretend it never happened. `ended` and `cancelled` are terminal. These four words are also
    the `ck_sessions_status` CHECK, and the partial unique index that permits one `live` row
    keys off this spelling."""

    SCHEDULED = "scheduled"
    LIVE = "live"
    ENDED = "ended"
    CANCELLED = "cancelled"


def _clean_title(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("title must not be blank")
    return value


# --- scheduling a satsang -----------------------------------------------------------------------


class SatsangSessionCreate(VersionedModel):
    """Request body for `POST /sessions`. Moderators and admins only.

    `scheduled_start_at` must carry a timezone. This is the first contract here to require
    that, deliberately: the column is `timestamptz`, so a naive value would be silently assumed
    rather than refused, and a satsang at "seven o'clock" in no particular zone is a reminder
    that fires in the wrong hour."""

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    speaker_user_id: str
    scheduled_start_at: AwareDatetime

    _strip_title = field_validator("title")(_clean_title)


# --- what a moderator or admin sees -------------------------------------------------------------


class SatsangSessionOut(VersionedModel):
    """One session, as the console sees it. **No stream key**: this is the shape every admin
    read returns, and a credential does not belong in a shape that is read often, logged, cached
    and widened later.

    `reminder_sent_at` is a nullable timestamp rather than a boolean so it can answer *when*.
    A boolean cannot survive a crash between sending and committing informatively, and cannot
    show that a reminder went out after the session had already started. It carries no rule
    tying it to `status`, because a reminder going out and the satsang then being cancelled is
    ordinary."""

    id: str
    title: str
    speaker_user_id: str
    speaker_name: str
    scheduled_start_at: datetime
    status: SatsangStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    reminder_sent_at: datetime | None = None
    created_by: str
    created_at: datetime

    @model_validator(mode="after")
    def _timestamps_agree_with_status(self) -> SatsangSessionOut:
        """The same coherence the database enforces (`ck_sessions_started_at_iff_started`,
        `ck_sessions_ended_at_iff_ended`, `ck_sessions_end_after_start`), stated once more where
        a client can see it rather than discovering it as a 500."""
        has_started = self.status in (SatsangStatus.LIVE, SatsangStatus.ENDED)
        if has_started != (self.started_at is not None):
            raise ValueError(
                f"status {self.status.value!r} and started_at disagree about whether it began"
            )
        if (self.status is SatsangStatus.ENDED) != (self.ended_at is not None):
            raise ValueError(
                f"status {self.status.value!r} and ended_at disagree about whether it finished"
            )
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("ended_at is before started_at")
        return self


class SatsangSessionCreated(VersionedModel):
    """Response for `POST /sessions` (201) and for rotating a key.

    **The only payload anywhere that carries the stream key.** Shown once; it cannot be fetched
    again, because the gateway stores only its SHA-256. `ingest_url` is the whole
    `rtmp://host:port/live/<key>` line, so the console can offer it as a QR for the speaker's
    broadcasting app instead of asking anyone to type a 43-character credential."""

    session: SatsangSessionOut
    stream_key: str
    ingest_url: str


# --- what an elder sees -------------------------------------------------------------------------


class SatsangSessionPublic(VersionedModel):
    """One session as an elder sees it: a title, who is speaking, when, and whether it is on now.

    The speaker is a **name**, never a row id, and none of our own bookkeeping (who scheduled
    it, when the reminder went out) is an elder's business. This is a separate model rather than
    `SatsangSessionOut` with fields excluded, because "remember to exclude it" is exactly how
    OPEN_QUESTIONS #25 happened."""

    id: str
    title: str
    speaker_name: str
    scheduled_start_at: datetime
    status: SatsangStatus
    # The playlist the join-to-listen screen plays. Present only while the session is live.
    hls_url: str | None = None

    @model_validator(mode="after")
    def _audio_only_while_live(self) -> SatsangSessionPublic:
        """A scheduled or ended session carrying a playlist would send an elder to a stream that
        is not there; a live one without it would give them nothing to play."""
        if (self.status is SatsangStatus.LIVE) != (self.hls_url is not None):
            raise ValueError(
                f"status {self.status.value!r} and hls_url disagree about whether it is on air"
            )
        return self


class SatsangSessionList(VersionedModel):
    """Response for `GET /sessions`: what an elder may see -- the upcoming ones, the one that is
    live, and any that were cancelled after a reminder had already gone out, so nobody arrives
    for a satsang that is off. Typed to the elder-facing shape, so widening the admin shape
    cannot leak through this one."""

    items: list[SatsangSessionPublic] = Field(default_factory=list)


class SatsangSessionAdminList(VersionedModel):
    """Response for the console's own listing. Every status, and still no stream key."""

    items: list[SatsangSessionOut] = Field(default_factory=list)
