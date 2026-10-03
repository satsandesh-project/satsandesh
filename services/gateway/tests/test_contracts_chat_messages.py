import pytest
from contracts.chat.common import AudioFormat, MediaRef, MessageKind, MessageStatus, TargetType
from contracts.chat.messages import AckOut, MessageIn, MessageOut
from pydantic import ValidationError

_CLIENT_MSG_ID = "11111111-1111-1111-1111-111111111111"


def test_text_message_requires_text() -> None:
    with pytest.raises(ValidationError):
        MessageIn(
            client_msg_id=_CLIENT_MSG_ID,
            target_type=TargetType.CIRCLE,
            target_id="circle-1",
            kind=MessageKind.TEXT,
        )


def test_voice_message_requires_media_ref() -> None:
    with pytest.raises(ValidationError):
        MessageIn(
            client_msg_id=_CLIENT_MSG_ID,
            target_type=TargetType.USER,
            target_id="user-2",
            kind=MessageKind.VOICE,
        )


def test_text_message_constructs_with_text() -> None:
    msg = MessageIn(
        client_msg_id=_CLIENT_MSG_ID,
        target_type=TargetType.CIRCLE,
        target_id="circle-1",
        kind=MessageKind.TEXT,
        text="Namaste",
        source_lang="te",
    )
    assert msg.target_type is TargetType.CIRCLE


def test_voice_message_constructs_with_media_ref() -> None:
    msg = MessageIn(
        client_msg_id=_CLIENT_MSG_ID,
        target_type=TargetType.USER,
        target_id="user-2",
        kind=MessageKind.VOICE,
        media_ref=MediaRef(uri="mock://audio/a.wav", format=AudioFormat.WAV_PCM16),
    )
    assert msg.media_ref is not None


def test_message_out_round_trips() -> None:
    out = MessageOut(
        id="msg-1",
        author_id="user-1",
        target_type=TargetType.CIRCLE,
        target_id="circle-1",
        kind=MessageKind.TEXT,
        text="Namaste",
        created_at="2026-08-17T09:00:00Z",
        status=MessageStatus.DELIVERED,
    )
    assert out.status is MessageStatus.DELIVERED
    assert out.media_ref is None


def test_message_out_carries_media_ref_for_a_voice_message() -> None:
    # Closes OPEN_QUESTIONS.md #1 -- a voice message's audio now has a
    # read-side wire representation.
    out = MessageOut(
        id="msg-2",
        author_id="user-1",
        target_type=TargetType.CIRCLE,
        target_id="circle-1",
        kind=MessageKind.VOICE,
        text=None,
        media_ref=MediaRef(uri="media:abc123", format=AudioFormat.WEBM_OPUS, duration_ms=4200),
        created_at="2026-08-17T09:00:00Z",
        status=MessageStatus.DELIVERED,
    )
    assert out.media_ref is not None
    assert out.media_ref.format is AudioFormat.WEBM_OPUS


def test_ack_out_carries_client_and_server_ids() -> None:
    ack = AckOut(client_msg_id=_CLIENT_MSG_ID, id="msg-1", status=MessageStatus.PENDING)
    assert str(ack.client_msg_id) == _CLIENT_MSG_ID
