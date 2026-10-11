"""Bhajan rooms in the mock chat gateway: what the elder app builds against. Same rules as the
real routes will have -- only a moderator creates and closes rooms and skips people, anyone in the
circle raises a hand, exactly one person leads, the lead (or a moderator) hands over, the first
person waiting may take an empty floor, only the lead's token can publish, and a stranger is told
the room does not exist."""

import pytest
from contracts.chat.mock import app as mock_app
from contracts.chat.mock import rooms as mock_rooms
from contracts.chat.rooms import QueueState, RoomList, RoomOut, RoomState, RoomStatus, RoomTokenOut
from fastapi.testclient import TestClient

MOD = {"X-Mock-User-Id": "moderator-1"}
ANAND = {"X-Mock-User-Id": "anand"}
BHAVYA = {"X-Mock-User-Id": "bhavya"}
CHITRA = {"X-Mock-User-Id": "chitra"}
STRANGER = {"X-Mock-User-Id": "stranger"}


@pytest.fixture
def client():
    with TestClient(mock_app.app) as test_client:
        yield test_client
    mock_rooms.reset()
    mock_app._circles.clear()
    mock_app._memberships.clear()


@pytest.fixture
def circle(client) -> str:
    made = client.post("/circles", json={"name": "Evening satsang"}, headers=MOD).json()
    circle_id = made["id"]
    for user, role in (
        ("moderator-1", "moderator"),
        ("anand", "member"),
        ("bhavya", "member"),
        ("chitra", "member"),
    ):
        client.post(f"/circles/{circle_id}/members", json={"user_id": user, "role": role})
    return circle_id


@pytest.fixture
def room(client, circle) -> str:
    resp = client.post("/rooms", json={"circle_id": circle, "title": "Bhajans"}, headers=MOD)
    assert resp.status_code == 201, resp.text
    return RoomOut.model_validate(resp.json()).id


def _state(client, room_id, headers) -> RoomState:
    resp = client.get(f"/rooms/{room_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    return RoomState.model_validate(resp.json())


def _raise(client, room_id, headers) -> RoomState:
    resp = client.post(f"/rooms/{room_id}/queue", headers=headers)
    assert resp.status_code == 200, resp.text
    return RoomState.model_validate(resp.json())


def _next(client, room_id, headers):
    return client.post(f"/rooms/{room_id}/next", headers=headers)


# --- who may create and close --------------------------------------------------------------


@pytest.mark.parametrize("headers", [ANAND, STRANGER])
def test_only_a_moderator_creates_a_room(client, circle, headers) -> None:
    resp = client.post("/rooms", json={"circle_id": circle, "title": "t"}, headers=headers)
    assert resp.status_code == 403
    assert mock_rooms._rooms == {}


def test_a_site_moderator_may_create_in_any_circle(client, circle) -> None:
    headers = {"X-Mock-User-Id": "someone", "X-Mock-Role": "moderator"}
    assert client.post("/rooms", json={"circle_id": circle, "title": "t"}, headers=headers)


def test_a_room_in_a_circle_that_does_not_exist_is_404(client) -> None:
    resp = client.post("/rooms", json={"circle_id": "nope", "title": "t"}, headers=MOD)
    assert resp.status_code == 404


def test_a_new_room_is_open_and_nobody_leads(client, room) -> None:
    state = _state(client, room, ANAND)
    assert state.room.status is RoomStatus.OPEN
    assert state.lead is None and state.queue == []
    assert state.you.state is None and not state.you.can_take_floor and not state.you.can_manage


def test_only_a_moderator_closes_a_room(client, room) -> None:
    assert client.post(f"/rooms/{room}/close", headers=ANAND).status_code == 403
    resp = client.post(f"/rooms/{room}/close", headers=MOD)
    assert RoomState.model_validate(resp.json()).room.status is RoomStatus.CLOSED
    # Closing twice is harmless, and nothing can be done in a closed room.
    assert client.post(f"/rooms/{room}/close", headers=MOD).status_code == 200
    assert client.post(f"/rooms/{room}/queue", headers=ANAND).status_code == 409
    assert client.post(f"/rooms/{room}/token", headers=ANAND).status_code == 409


# --- who can see a room --------------------------------------------------------------------


def test_a_stranger_is_told_the_room_does_not_exist(client, room) -> None:
    assert client.get(f"/rooms/{room}", headers=STRANGER).status_code == 404
    assert client.post(f"/rooms/{room}/queue", headers=STRANGER).status_code == 404
    assert client.post(f"/rooms/{room}/token", headers=STRANGER).status_code == 404
    assert client.get("/rooms/not-a-room", headers=ANAND).status_code == 404


def test_the_list_shows_open_rooms_of_my_circles_only(client, circle, room) -> None:
    other = client.post("/circles", json={"name": "Other"}, headers=MOD).json()["id"]
    client.post(f"/circles/{other}/members", json={"user_id": "moderator-1", "role": "moderator"})
    client.post("/rooms", json={"circle_id": other, "title": "Elsewhere"}, headers=MOD)

    mine = RoomList.model_validate(client.get("/rooms", headers=ANAND).json()).items
    assert [r.id for r in mine] == [room]
    assert client.get("/rooms", headers=STRANGER).json()["items"] == []

    client.post(f"/rooms/{room}/close", headers=MOD)
    assert client.get("/rooms", headers=ANAND).json()["items"] == []


# --- the queue and the floor ----------------------------------------------------------------


def test_raising_a_hand_joins_the_queue_in_order_and_is_idempotent(client, room) -> None:
    _raise(client, room, ANAND)
    _raise(client, room, BHAVYA)
    again = _raise(client, room, ANAND)  # a second tap changes nothing
    assert [(e.user_id, e.position) for e in again.queue] == [("anand", 1), ("bhavya", 2)]
    assert again.you.state is QueueState.WAITING and again.you.position == 1
    assert again.room.waiting_count == 2


def test_with_nobody_leading_only_the_first_in_line_or_a_moderator_may_take_the_floor(
    client, room
) -> None:
    _raise(client, room, ANAND)
    _raise(client, room, BHAVYA)
    assert _state(client, room, ANAND).you.can_take_floor is True
    assert _state(client, room, BHAVYA).you.can_take_floor is False
    assert _state(client, room, MOD).you.can_take_floor is True
    assert _state(client, room, CHITRA).you.can_take_floor is False

    assert _next(client, room, BHAVYA).status_code == 403
    taken = RoomState.model_validate(_next(client, room, ANAND).json())
    assert taken.lead and taken.lead.user_id == "anand" and taken.you.is_lead
    assert [e.user_id for e in taken.queue] == ["bhavya"]


def test_the_handoff_is_one_request_and_the_old_lead_is_done(client, room) -> None:
    _raise(client, room, ANAND)
    _raise(client, room, BHAVYA)
    _next(client, room, ANAND)  # anand leads

    # Bhavya cannot grab the floor from the lead; the lead hands over.
    assert _next(client, room, BHAVYA).status_code == 403
    handed = RoomState.model_validate(_next(client, room, ANAND).json())
    assert handed.lead and handed.lead.user_id == "bhavya"
    assert handed.queue == []
    assert _state(client, room, ANAND).you.is_lead is False
    assert _state(client, room, ANAND).you.state is QueueState.DONE


def test_exactly_one_person_leads_at_a_time(client, room) -> None:
    for who in (ANAND, BHAVYA, CHITRA):
        _raise(client, room, who)
    _next(client, room, ANAND)
    leaders = [e for e in mock_rooms._entries[room] if e["state"] is QueueState.LEADING]
    assert len(leaders) == 1


def test_the_lead_stepping_down_with_nobody_waiting_leaves_the_floor_empty(client, room) -> None:
    _raise(client, room, ANAND)
    _next(client, room, ANAND)
    stepped = RoomState.model_validate(_next(client, room, ANAND).json())
    assert stepped.lead is None
    # And with nobody waiting and nobody leading there is nothing to hand over.
    assert _next(client, room, MOD).status_code == 409


def test_a_moderator_may_hand_over_at_any_time(client, room) -> None:
    _raise(client, room, ANAND)
    _raise(client, room, BHAVYA)
    _next(client, room, ANAND)
    handed = RoomState.model_validate(_next(client, room, MOD).json())
    assert handed.lead and handed.lead.user_id == "bhavya"


def test_the_lead_leaving_is_a_handoff_and_a_waiting_person_leaving_just_leaves(
    client, room
) -> None:
    _raise(client, room, ANAND)
    _raise(client, room, BHAVYA)
    _raise(client, room, CHITRA)
    _next(client, room, ANAND)

    left = RoomState.model_validate(client.delete(f"/rooms/{room}/queue", headers=BHAVYA).json())
    assert [e.user_id for e in left.queue] == ["chitra"] and left.lead.user_id == "anand"

    out = RoomState.model_validate(client.delete(f"/rooms/{room}/queue", headers=ANAND).json())
    assert out.lead and out.lead.user_id == "chitra"


def test_a_person_may_raise_a_hand_again_after_a_turn(client, room) -> None:
    _raise(client, room, ANAND)
    _next(client, room, ANAND)
    _next(client, room, ANAND)  # steps down
    again = _raise(client, room, ANAND)
    assert again.you.state is QueueState.WAITING and again.you.position == 1


def test_only_a_moderator_skips_someone_and_only_someone_waiting(client, room) -> None:
    _raise(client, room, ANAND)
    _raise(client, room, BHAVYA)
    assert client.post(f"/rooms/{room}/queue/bhavya/skip", headers=ANAND).status_code == 403
    assert client.post(f"/rooms/{room}/queue/nobody/skip", headers=MOD).status_code == 404

    skipped = RoomState.model_validate(
        client.post(f"/rooms/{room}/queue/anand/skip", headers=MOD).json()
    )
    assert [e.user_id for e in skipped.queue] == ["bhavya"] and skipped.queue[0].position == 1
    assert _state(client, room, ANAND).you.state is QueueState.REMOVED


# --- the token --------------------------------------------------------------------------------


def test_only_the_lead_gets_a_token_that_can_publish(client, room) -> None:
    _raise(client, room, ANAND)
    before = RoomTokenOut.model_validate(client.post(f"/rooms/{room}/token", headers=ANAND).json())
    assert before.can_publish is False and before.identity == "anand"

    _next(client, room, ANAND)
    lead = RoomTokenOut.model_validate(client.post(f"/rooms/{room}/token", headers=ANAND).json())
    listener = RoomTokenOut.model_validate(
        client.post(f"/rooms/{room}/token", headers=BHAVYA).json()
    )
    assert lead.can_publish is True
    assert listener.can_publish is False


def test_the_state_names_people_by_their_name_when_the_mock_knows_it(client, room) -> None:
    from contracts.chat.admin_org import AdminUserOut, SiteRole
    from contracts.chat.mock import admin_org as mock_admin

    mock_admin._users["anand"] = AdminUserOut(
        id="anand",
        name="Anand Rao",
        preferred_language="te",
        role=SiteRole.ELDER,
        created_at="2026-10-12T09:30:00Z",
    )
    try:
        _raise(client, room, ANAND)
        assert _state(client, room, ANAND).queue[0].name == "Anand Rao"
        assert _state(client, room, BHAVYA).queue[0].name == "Anand Rao"
    finally:
        mock_admin._users.pop("anand", None)
