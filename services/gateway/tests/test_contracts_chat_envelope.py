import pytest
from contracts.chat.common import MessageStatus, TargetType
from contracts.chat.envelope import (
    FrameType,
    MessageAckFrame,
    RawFrame,
    SyncBatch,
    SyncRequest,
)
from contracts.chat.messages import AckOut
from pydantic import ValidationError


def test_frame_type_enumerates_every_wire_type() -> None:
    assert {t.value for t in FrameType} == {
        "message.send",
        "message.ack",
        "message.new",
        "sync.request",
        "sync.batch",
        "error",
    }


def test_raw_frame_parses_type_before_data_is_typed() -> None:
    # Every inbound frame is parsed as RawFrame first so the dispatcher can
    # read `type` before deciding which payload model validates `data`.
    raw = RawFrame.model_validate({"type": "message.ack", "data": {"anything": "goes-here"}})
    assert raw.type is FrameType.MESSAGE_ACK


def test_raw_frame_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        RawFrame.model_validate({"type": "message.explode", "data": {}})


def test_message_ack_frame_validates_data_against_ack_out() -> None:
    frame = MessageAckFrame.model_validate(
        {
            "type": "message.ack",
            "data": {
                "client_msg_id": "11111111-1111-1111-1111-111111111111",
                "id": "msg-1",
                "status": "pending",
            },
        }
    )
    assert isinstance(frame.data, AckOut)
    assert frame.data.status is MessageStatus.PENDING


def test_sync_request_defaults_since_id_to_none_for_full_history() -> None:
    req = SyncRequest(target_type=TargetType.CIRCLE, target_id="circle-1")
    assert req.since_id is None
    assert req.limit == 50


def test_sync_batch_carries_messages_for_one_conversation() -> None:
    batch = SyncBatch(
        target_type=TargetType.CIRCLE, target_id="circle-1", messages=[], has_more=False
    )
    assert batch.messages == []
