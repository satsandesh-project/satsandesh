import pytest
from contracts.chat.common import MediaRef, MessageKind, MessageStatus, TargetType
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
        media_ref=MediaRef(uri="mock://audio/a.wav"),
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


def test_ack_out_carries_client_and_server_ids() -> None:
    ack = AckOut(client_msg_id=_CLIENT_MSG_ID, id="msg-1", status=MessageStatus.PENDING)
    assert str(ack.client_msg_id) == _CLIENT_MSG_ID
