"""contracts/chat/rooms.py: bhajan rooms with floor control.

Golden fixtures (one canonical example each; they fail if a field drifts without a version bump)
and the rules the models enforce on their own.
"""

import json
from pathlib import Path

import pytest
from contracts.chat.common import CONTRACTS_VERSION
from contracts.chat.rooms import (
    MAX_PARTICIPANTS,
    MAX_TITLE_LENGTH,
    QueueEntryOut,
    QueueState,
    RoomCreate,
    RoomList,
    RoomOut,
    RoomState,
    RoomStatus,
    RoomTokenOut,
    YourPlace,
)
from pydantic import BaseModel, ValidationError

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chat"

MODELS_BY_FIXTURE: dict[str, type[BaseModel]] = {
    "room_create.json": RoomCreate,
    "room_out.json": RoomOut,
    "room_list.json": RoomList,
    "room_state.json": RoomState,
    "room_token_out.json": RoomTokenOut,
}


@pytest.mark.parametrize("filename,model_cls", MODELS_BY_FIXTURE.items())
def test_golden_fixture_parses_and_round_trips(filename, model_cls) -> None:
    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    assert model_cls.model_validate(raw).model_dump(mode="json") == raw
    assert raw["contract_version"] == CONTRACTS_VERSION


def test_a_room_title_is_trimmed_and_may_not_be_blank_or_huge() -> None:
    assert RoomCreate(circle_id="c", title="  Evening bhajans  ").title == "Evening bhajans"
    for bad in ("", "   ", "x" * (MAX_TITLE_LENGTH + 1)):
        with pytest.raises(ValidationError):
            RoomCreate(circle_id="c", title=bad)


def test_a_queue_position_starts_at_one() -> None:
    assert QueueEntryOut(user_id="u", name="n", position=1).state is QueueState.WAITING
    with pytest.raises(ValidationError):
        QueueEntryOut(user_id="u", name="n", position=0)
    with pytest.raises(ValidationError):
        YourPlace(position=0)


def test_a_person_who_has_not_raised_a_hand_has_no_place() -> None:
    place = YourPlace()
    assert place.state is None and place.position is None
    assert not (place.is_lead or place.can_take_floor or place.can_manage)


def test_a_new_room_is_open_with_no_lead() -> None:
    room = RoomOut.model_validate(
        {
            "id": "r",
            "circle_id": "c",
            "title": "t",
            "status": "open",
            "created_by": "u",
            "created_at": "2026-10-12T09:30:00Z",
            "livekit_room": "room-r",
        }
    )
    assert room.status is RoomStatus.OPEN and room.lead is None and room.waiting_count == 0
    assert room.closed_at is None


def test_the_token_cannot_publish_unless_it_says_so() -> None:
    token = RoomTokenOut.model_validate(
        {
            "token": "t",
            "url": "wss://example/livekit",
            "identity": "u",
            "expires_at": "2026-10-12T10:30:00Z",
        }
    )
    assert token.can_publish is False


def test_a_room_is_for_about_fifty_people() -> None:
    assert MAX_PARTICIPANTS == 50
