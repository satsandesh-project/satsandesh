from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from contracts.chat.common import (
    MediaRef,
    MessageKind,
    MessageStatus,
    TargetType,
    VersionedModel,
)
from contracts.chat.renderings import MAX_RENDERINGS_PER_MESSAGE, Rendering


class MessageIn(VersionedModel):
    """What a client sends to create a message. `id` is deliberately absent
    here — the server assigns it. See DECISIONS.md #2 for why `client_msg_id`
    is client-generated and `id` (on MessageOut/AckOut) is server-generated."""

    client_msg_id: UUID
    target_type: TargetType
    target_id: str
    kind: MessageKind
    text: str | None = None
    media_ref: MediaRef | None = None
    source_lang: str | None = None

    @model_validator(mode="after")
    def _require_payload_matching_kind(self) -> "MessageIn":
        if self.kind is MessageKind.TEXT and not self.text:
            raise ValueError("text is required when kind is 'text'")
        if self.kind is MessageKind.VOICE and self.media_ref is None:
            raise ValueError("media_ref is required when kind is 'voice'")
        return self


class MessageOut(VersionedModel):
    """What a client reads back — over HTTP sync or a `message.new` WS
    frame. `id` is the server-authoritative identifier; it is kept as an
    opaque string rather than constrained to UUID so the service layer can
    later pick a sortable id scheme (e.g. UUIDv7/ULID) without a contract
    change — see DECISIONS.md #3.

    `media_ref` (Week 6): a `kind: "voice"` message previously had no
    read-side representation of where its audio actually is — `MediaRef`
    only ever appeared on `MessageIn`. Closes OPEN_QUESTIONS.md #1. `None`
    for a text message; for a voice message, whatever `MediaRef` the storage
    layer resolved at write time (see DECISIONS.md #15) — not necessarily
    byte-identical to what the client uploaded, since a backend is free to
    transcode before storing.

    `renderings` (Week 7): what the pipeline made of this message for each
    language it was rendered into -- see contracts/chat/renderings.py and
    DECISIONS.md #16. Empty for a message nothing was rendered for (a text
    message to a receiver who shares its language, a message still moving
    through the pipeline, one held or blocked) and for any producer that
    predates the field, so an older payload without it still parses.
    At most one rendering per language."""

    id: str
    author_id: str
    target_type: TargetType
    target_id: str
    kind: MessageKind
    text: str | None = None
    media_ref: MediaRef | None = None
    renderings: list[Rendering] = Field(default_factory=list, max_length=MAX_RENDERINGS_PER_MESSAGE)
    created_at: datetime
    status: MessageStatus

    @model_validator(mode="after")
    def _one_rendering_per_language(self) -> "MessageOut":
        languages = [r.language for r in self.renderings]
        duplicates = sorted({lang for lang in languages if languages.count(lang) > 1})
        if duplicates:
            raise ValueError(f"duplicate rendering language(s): {duplicates}")
        return self


class AckOut(VersionedModel):
    """Synchronous reply to a `POST /messages` or a `message.send` WS frame.
    Carries both ids so the client can reconcile its optimistically-rendered
    local message (keyed by `client_msg_id`) with the server's authoritative
    `id` in one step."""

    client_msg_id: UUID
    id: str
    status: MessageStatus


class DeliveredIn(VersionedModel):
    """`data` for a `message.delivered` WS frame — the recipient confirming
    actual receipt of a `message.new`, not just that the server attempted a
    push. Works for both a DM's single recipient and a circle's N
    recipients (see app/ws.py's `_handle_message_delivered`) — the wire
    shape doesn't need to know which, since it's just "I confirm I have
    this message," and the two cases differ only in how the server
    aggregates and reports it back (see MessageStatusOut below)."""

    message_id: str


class MessageStatusOut(VersionedModel):
    """`data` for a `message.status` WS frame — pushed to the *sender's*
    connected devices when a message's status changes after the initial
    ack, so their UI can move off whatever it showed for the ack without
    polling.

    For a DM (a single, well-defined recipient): `status` transitions
    `sent` -> `delivered`, `delivered_count`/`member_count` stay unset.

    For a circle message (N recipients, no single "delivered" moment):
    `status` is NOT reused to mean "fully delivered" -- that would
    misrepresent a partial count as if everyone had it, and would also
    collide with `messages.status`'s existing, unrelated meaning (the
    sender-side pending/sent/cancelled undo-window lifecycle, unchanged
    by this). Instead `delivered_count`/`member_count` carry the real
    aggregate progress (e.g. "3/13"), pushed again each time a new
    member's confirmation arrives -- `status` stays whatever the
    message's actual lifecycle status already was."""

    id: str
    status: MessageStatus
    delivered_count: int | None = None
    member_count: int | None = None
