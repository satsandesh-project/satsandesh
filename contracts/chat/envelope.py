from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from contracts.chat.common import TargetType, VersionedModel
from contracts.chat.errors import ErrorPayload
from contracts.chat.messages import (
    AckOut,
    DeliveredIn,
    MessageIn,
    MessageOut,
    MessageStatusOut,
)

DataT = TypeVar("DataT", bound=BaseModel)


class FrameType(str, Enum):
    MESSAGE_SEND = "message.send"
    MESSAGE_ACK = "message.ack"
    MESSAGE_NEW = "message.new"
    MESSAGE_DELIVERED = "message.delivered"
    MESSAGE_STATUS = "message.status"
    SYNC_REQUEST = "sync.request"
    SYNC_BATCH = "sync.batch"
    ERROR = "error"


class Frame(BaseModel, Generic[DataT]):
    """Every WS frame on the wire is `{"type": ..., "data": {...}}`. Use a
    parametrized alias below (e.g. `MessageAckFrame`) when the frame's type
    is already known and you want `data` validated against a specific
    payload model."""

    type: FrameType
    data: DataT


class RawFrame(BaseModel):
    """First-pass parse for an inbound frame of unknown type: validates
    `type` against the closed enum but leaves `data` untyped so the caller
    can read `type` and then re-validate `data` against the right payload
    model. This is how contracts/chat/mock/app.py dispatches on a single
    WS connection that carries more than one frame type."""

    type: FrameType
    data: dict


class SyncRequest(BaseModel):
    """`data` for a `sync.request` frame, and the query-param shape for
    `GET /messages`. The cursor is per-conversation (`target_type` +
    `target_id`), not a single global per-user cursor — see DECISIONS.md
    #7."""

    target_type: TargetType
    target_id: str
    since_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class SyncBatch(VersionedModel):
    """`data` for a `sync.batch` frame, and the response body for
    `GET /messages` — deliberately the same shape in both places so a
    client's sync-merge logic doesn't need two code paths for the two
    transports."""

    target_type: TargetType
    target_id: str
    messages: list[MessageOut]
    has_more: bool


MessageSendFrame = Frame[MessageIn]
MessageAckFrame = Frame[AckOut]
MessageNewFrame = Frame[MessageOut]
MessageDeliveredFrame = Frame[DeliveredIn]
MessageStatusFrame = Frame[MessageStatusOut]
SyncRequestFrame = Frame[SyncRequest]
SyncBatchFrame = Frame[SyncBatch]
ErrorFrame = Frame[ErrorPayload]
