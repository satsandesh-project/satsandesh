from enum import Enum

from pydantic import BaseModel

CONTRACTS_VERSION = "0.1.0"
"""
Schema-shape version for everything in contracts/chat/. Mirrors
contracts/ai/common.CONTRACTS_VERSION in purpose — bump when any field is
added, renamed, retyped, or removed on a payload here, so a downstream
consumer can tell which shape it is looking at. The two version strings are
independent counters: contracts/ai/ and contracts/chat/ are owned and
evolved on different schedules by different people, so pinning them to one
number would force one package's churn onto the other for no shared benefit.
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


class MediaRef(BaseModel):
    """A reference to stored media, never embedded bytes — same reasoning as
    contracts/ai/common.AudioRef, but deliberately not that type. See
    DECISIONS.md #5 for why contracts/chat/ does not import contracts/ai/."""

    uri: str
    duration_ms: int | None = None
