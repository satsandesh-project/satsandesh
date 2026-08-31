"""
Envelope is a standalone proposal for the project-wide message shape — the gateway
owner defines the real one. Nothing here asserts my payload models depend on it;
test_payload_modules_do_not_import_envelope instead asserts they do NOT.
"""

import uuid
from datetime import UTC, datetime

import pytest
from contracts.ai.envelope import Envelope, MessageType
from contracts.ai.moderation import ModerationRequest
from pydantic import ValidationError


def test_envelope_round_trips_a_payload() -> None:
    payload = ModerationRequest(text="Jai Shri Krishna")
    env = Envelope[ModerationRequest](
        type=MessageType.AI_MODERATION_REQUEST,
        id=uuid.uuid4(),
        ts=datetime.now(UTC),
        payload=payload,
    )
    restored = Envelope[ModerationRequest].model_validate_json(env.model_dump_json())
    assert restored.payload == payload
    assert restored.type is MessageType.AI_MODERATION_REQUEST


def test_envelope_rejects_unknown_message_type() -> None:
    with pytest.raises(ValidationError):
        Envelope[ModerationRequest].model_validate(
            {
                "type": "not.a.real.type",
                "id": str(uuid.uuid4()),
                "ts": datetime.now(UTC).isoformat(),
                "payload": {"text": "hi"},
            }
        )


def test_envelope_rejects_non_uuid_id() -> None:
    with pytest.raises(ValidationError):
        Envelope[ModerationRequest].model_validate(
            {
                "type": MessageType.AI_MODERATION_REQUEST.value,
                "id": "not-a-uuid",
                "ts": datetime.now(UTC).isoformat(),
                "payload": {"text": "hi"},
            }
        )


def test_envelope_rejects_malformed_timestamp() -> None:
    with pytest.raises(ValidationError):
        Envelope[ModerationRequest].model_validate(
            {
                "type": MessageType.AI_MODERATION_REQUEST.value,
                "id": str(uuid.uuid4()),
                "ts": "not-a-date",
                "payload": {"text": "hi"},
            }
        )


def test_payload_modules_do_not_import_envelope() -> None:
    """Requests/responses must nest inside the caller's envelope, not depend on it."""
    from contracts.ai import moderation, pivot, render, transcribe

    for module in (transcribe, pivot, render, moderation):
        assert "Envelope" not in vars(module), f"{module.__name__} must not import Envelope"
