from datetime import datetime
from uuid import UUID

from pydantic import model_validator

from contracts.chat.common import (
    MediaRef,
    MessageKind,
    MessageStatus,
    TargetType,
    VersionedModel,
)


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
    change — see DECISIONS.md #3."""

    id: str
    author_id: str
    target_type: TargetType
    target_id: str
    kind: MessageKind
    text: str | None = None
    created_at: datetime
    status: MessageStatus


class AckOut(VersionedModel):
    """Synchronous reply to a `POST /messages` or a `message.send` WS frame.
    Carries both ids so the client can reconcile its optimistically-rendered
    local message (keyed by `client_msg_id`) with the server's authoritative
    `id` in one step."""

    client_msg_id: UUID
    id: str
    status: MessageStatus
