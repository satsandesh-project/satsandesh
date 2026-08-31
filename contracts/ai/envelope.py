"""
PROPOSAL, NOT A DEPENDENCY.

This documents the project-wide message shape my payloads are expected to nest
inside: { "type", "id", "ts", "payload" }. No request/response model in this
package imports or subclasses Envelope — the gateway owner defines the real
envelope, and the caller (gateway) is responsible for wrapping my payloads in it.
This file exists so my mock server and tests can round-trip a realistic envelope
shape standalone, ahead of the real gateway existing.
"""

from datetime import datetime
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel

PayloadT = TypeVar("PayloadT", bound=BaseModel)


class MessageType(str, Enum):
    AI_TRANSCRIBE_REQUEST = "ai.transcribe.request"
    AI_TRANSCRIBE_RESPONSE = "ai.transcribe.response"
    AI_PIVOT_REQUEST = "ai.pivot.request"
    AI_PIVOT_RESPONSE = "ai.pivot.response"
    AI_RENDER_REQUEST = "ai.render.request"
    AI_RENDER_RESPONSE = "ai.render.response"
    AI_MODERATION_REQUEST = "ai.moderation.request"
    AI_MODERATION_DECISION = "ai.moderation.decision"
    AI_PIPELINE_ERROR = "ai.pipeline.error"


class Envelope(BaseModel, Generic[PayloadT]):
    type: MessageType
    id: UUID
    ts: datetime
    payload: PayloadT
