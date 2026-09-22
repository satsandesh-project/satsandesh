import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

CONTRACTS_VERSION = "0.2.0"
"""
Schema-shape version for everything in contracts/chat/. Mirrors
contracts/ai/common.CONTRACTS_VERSION in purpose — bump when any field is
added, renamed, retyped, or removed on a payload here, so a downstream
consumer can tell which shape it is looking at. The two version strings are
independent counters: contracts/ai/ and contracts/chat/ are owned and
evolved on different schedules by different people, so pinning them to one
number would force one package's churn onto the other for no shared benefit.

0.1.0 -> 0.2.0 (Week 6): MediaRef gained a required `format` field and a
constrained `uri` shape, and MessageOut gained `media_ref` — see
DECISIONS.md #13-#15 and OPEN_QUESTIONS.md #1 (now closed). No agreed
semver policy yet (OPEN_QUESTIONS.md #6) — this is a visible bump for a
real shape change, not a claim about major-vs-minor.
"""


class VersionedModel(BaseModel):
    """Base for every chat payload that travels over the wire on its own —
    HTTP request/response bodies and WS frame `data` payloads. Nested value
    objects (MediaRef) don't get their own stamp; see DECISIONS.md, same
    reasoning as contracts/ai/common.py's VersionedModel."""

    contract_version: str = CONTRACTS_VERSION


class TargetType(str, Enum):
    """Discriminant for the polymorphic (target_type, target_id) pair used
    everywhere a message needs to say who/what it's addressed to. See
    DECISIONS.md #1 for why this is a type+id pair rather than two nullable
    columns."""

    USER = "user"
    CIRCLE = "circle"


class MessageKind(str, Enum):
    TEXT = "text"
    VOICE = "voice"


class MessageStatus(str, Enum):
    """Chat-level delivery lifecycle, not a moderation label — see
    DECISIONS.md #6 for why this is a separate enum from anything in
    contracts/ai/moderation.py.

    SENT and CANCELLED restored here after being lost when PR #18 swapped
    the active gateway and reverted this file to an older state along
    with it -- see docs/OWNERSHIP.md and docs/BACKBONE_DECISION_BRIEF.md.
    Both are real states services/gateway/app/messages.py and app/undo.py
    already assume: PENDING -> SENT happens when app/undo.py's 30-second
    window elapses and the message actually fans out (see
    schedule_fan_out); PENDING -> CANCELLED is the undo path itself. The
    Postgres CHECK constraint on messages.status (migration
    ee7195a99a19) already includes both values -- this enum not matching
    the DB is exactly the kind of drift that made the app crash with an
    AttributeError deep inside a background task, silently, per M1's
    report."""

    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    HELD = "held"
    BLOCKED = "blocked"
    FAILED = "failed"


class AudioFormat(str, Enum):
    """Deliberately a separate enum from contracts.ai.common.AudioFormat, not
    an import of it — same reasoning as MessageStatus vs
    contracts.ai.moderation.ModerationAction (DECISIONS.md #6), on top of
    DECISIONS.md #5's blanket "no import" rule. Values match
    contracts.ai.common.AudioFormat's three one-for-one (WAV_PCM16, OGG_OPUS,
    MP3), plus WEBM_OPUS, which contracts/ai/ does not have yet: it's what a
    browser's MediaRecorder actually produces in Chrome/Edge
    (`audio/webm;codecs=opus`), not the Ogg container contracts/ai/'s ASR
    service currently expects. See OPEN_QUESTIONS.md #9 for the open,
    cross-lane question this leaves — whether contracts/ai/ eventually gains
    its own WEBM_OPUS value, or whether a transcode step normalizes it before
    the AI pipeline ever sees it."""

    WEBM_OPUS = "webm_opus"
    OGG_OPUS = "ogg_opus"
    WAV_PCM16 = "wav_pcm16"
    MP3 = "mp3"


_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:\S+$")


def validate_media_uri(value: str) -> str:
    """Shared by MediaRef (below) and MediaUploadOut (media.py) so the two
    don't drift into checking the shape differently -- both describe the
    same "where does the audio live" concept, one as a value nested in a
    message, the other as a fresh upload response. See MediaRef's own
    docstring for what this does and does not constrain."""
    if not _URI_SCHEME_RE.match(value):
        raise ValueError(
            "uri must be a scheme-qualified URI (e.g. 'media:<id>', "
            "'s3://bucket/key') -- a bare filename or path doesn't say "
            "where a stored artifact actually lives"
        )
    return value


class MediaRef(BaseModel):
    """A reference to stored media, never embedded bytes — same reasoning as
    contracts/ai/common.AudioRef, but deliberately not that type. See
    DECISIONS.md #5 for why contracts/chat/ does not import contracts/ai/.

    `uri` is constrained to a scheme-qualified shape (`<scheme>:...`) rather
    than left a bare string — see DECISIONS.md #13 for why this doesn't pick
    a specific scheme (no hostname, bucket, or local-path shape baked in) and
    what "media:<id>", this package's own indirection scheme, means.
    `format` is required, not inferred from the bytes — same "declared, not
    sniffed" philosophy as contracts.ai.common.AudioRef.format and
    MessageIn.kind; see DECISIONS.md #14."""

    uri: str
    format: AudioFormat
    duration_ms: int | None = Field(default=None, ge=0)

    @field_validator("uri")
    @classmethod
    def _uri_must_be_scheme_qualified(cls, value: str) -> str:
        return validate_media_uri(value)
