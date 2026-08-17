import pytest
from contracts.chat.common import MediaRef, MessageKind, MessageStatus, TargetType
from pydantic import ValidationError


def test_target_type_is_a_closed_enum() -> None:
    assert TargetType.USER.value == "user"
    assert TargetType.CIRCLE.value == "circle"


def test_message_kind_is_a_closed_enum() -> None:
    assert {k.value for k in MessageKind} == {"text", "voice"}


def test_message_status_is_a_closed_enum() -> None:
    assert {s.value for s in MessageStatus} == {
        "pending",
        "delivered",
        "held",
        "blocked",
        "failed",
    }


def test_media_ref_requires_uri() -> None:
    with pytest.raises(ValidationError):
        MediaRef.model_validate({})
    ref = MediaRef(uri="mock://audio/a.wav", duration_ms=1200)
    assert ref.duration_ms == 1200
