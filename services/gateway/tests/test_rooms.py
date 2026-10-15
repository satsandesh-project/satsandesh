"""Bhajan rooms with floor control (`/rooms/*`, app/rooms.py).

The rules that matter most, each with a test that fails if it is broken:

- exactly one person leads (the database refuses a second lead, and hand-overs are serialised);
- the server decides who may speak: a join token can publish only for the current lead, and a
  hand-over silences the old lead BEFORE it allows the new one, through LiveKit's admin API;
- if LiveKit cannot be told, the hand-over does not happen (502, nothing changed);
- only a moderator creates and closes rooms, skips people and hands over at any time;
- a person outside the room's circle is told the room does not exist.

LiveKit is replaced by a fake that records every call; no LiveKit runs in these tests. The real
LiveKit calls are covered, separately, in tests/test_livekit.py. These tests authenticate with real
signed tokens so the whole auth path is exercised.
"""

import base64
import hashlib
import json
import threading
import uuid

import pytest
from contracts.chat.rooms import QueueState, RoomList, RoomOut, RoomState, RoomStatus, RoomTokenOut
from jose import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import livekit
from app.config import get_settings
from app.db.models import Circle, Membership
from app.db.models import User as DbUser
from app.db.rooms import LeadQueueEntry, Room, lock_room
from app.livekit import LiveKitUnavailable
from app.tokens import issue_token

SECRET = "a-livekit-api-secret-of-at-least-thirty-two-bytes"
API_KEY = "devkey"


class FakeLiveKit:
    """Records what the gateway tells LiveKit, in order. `fail_on` makes the n-th
    `set_can_publish` call (1-based) raise, as an unreachable LiveKit would."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_on: set[int] = set()
        self.connected: set[str] = set()  # identities that are in the room; others -> False
        self._publish_calls = 0
        self.fail_delete = False

    def create_room(self, name: str) -> None:
        self.calls.append(("create_room", name))

    def set_can_publish(self, room: str, identity: str, can_publish: bool) -> bool:
        self._publish_calls += 1
        self.calls.append(("set_can_publish", room, identity, can_publish))
        if self._publish_calls in self.fail_on:
            raise LiveKitUnavailable("simulated outage")
        return identity in self.connected or not self.connected

    def delete_room(self, name: str) -> None:
        self.calls.append(("delete_room", name))
        if self.fail_delete:
            raise LiveKitUnavailable("simulated outage")

    def publishes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] == "set_can_publish"]


@pytest.fixture
def lk(monkeypatch) -> FakeLiveKit:
    settings = get_settings()
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SECRET)
    monkeypatch.setattr(settings, "LIVEKIT_PUBLIC_URL", "wss://example.test/livekit")
    monkeypatch.setattr(settings, "LIVEKIT_INTERNAL_URL", "http://livekit:7880")
    fake = FakeLiveKit()
    monkeypatch.setattr(livekit, "get_livekit_admin", lambda: fake)
    return fake


def _user(db_session, name, role="elder"):
    user = DbUser(name=name, preferred_language="te", role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _as(user):
    return {"Authorization": f"Bearer {issue_token(user.id)}"}


class World:
    """A circle with a moderator and three members, a stranger, and a site admin."""

    def __init__(self, db_session):
        self.db = db_session
        self.mod = _user(db_session, "Meera (moderator)")
        self.anand = _user(db_session, "Anand")
        self.bhavya = _user(db_session, "Bhavya")
        self.chitra = _user(db_session, "Chitra")
        self.stranger = _user(db_session, "Stranger")
        self.site_mod = _user(db_session, "Site moderator", role="moderator")
        circle = Circle(name="Evening satsang", kind="group", created_by=self.mod.id)
        db_session.add(circle)
        db_session.commit()
        self.circle = circle
        for person, role in (
            (self.mod, "moderator"),
            (self.anand, "member"),
            (self.bhavya, "member"),
            (self.chitra, "member"),
        ):
            db_session.add(Membership(circle_id=circle.id, user_id=person.id, role=role))
        db_session.commit()


@pytest.fixture
def w(db_session) -> World:
    return World(db_session)


@pytest.fixture
def room_id(client, w) -> str:
    resp = client.post(
        "/rooms", json={"circle_id": str(w.circle.id), "title": "Bhajans"}, headers=_as(w.mod)
    )
    assert resp.status_code == 201, resp.text
    return RoomOut.model_validate(resp.json()).id


def _state(client, room_id, person) -> RoomState:
    resp = client.get(f"/rooms/{room_id}", headers=_as(person))
    assert resp.status_code == 200, resp.text
    return RoomState.model_validate(resp.json())


def _raise(client, room_id, person) -> RoomState:
    resp = client.post(f"/rooms/{room_id}/queue", headers=_as(person))
    assert resp.status_code == 200, resp.text
    return RoomState.model_validate(resp.json())


def _next(client, room_id, person):
    return client.post(f"/rooms/{room_id}/next", headers=_as(person))


def _lead_id(client, room_id, person) -> str | None:
    lead = _state(client, room_id, person).lead
    return lead.user_id if lead else None


# --- creating, listing, seeing ------------------------------------------------------------------------


def test_every_route_needs_a_signed_in_user(client, room_id) -> None:
    for method, path in (
        ("get", "/rooms"),
        ("post", "/rooms"),
        ("get", f"/rooms/{room_id}"),
        ("post", f"/rooms/{room_id}/token"),
        ("post", f"/rooms/{room_id}/queue"),
        ("delete", f"/rooms/{room_id}/queue"),
        ("post", f"/rooms/{room_id}/next"),
        ("post", f"/rooms/{room_id}/close"),
    ):
        assert getattr(client, method)(path).status_code == 401, (method, path)


def test_a_moderator_creates_a_room_and_it_starts_open_with_no_lead(client, w, room_id) -> None:
    state = _state(client, room_id, w.anand)
    assert state.room.status is RoomStatus.OPEN
    assert state.room.title == "Bhajans"
    assert state.room.created_by == str(w.mod.id)
    assert state.room.livekit_room == f"room-{room_id}"
    assert state.lead is None and state.queue == []
    assert state.you.state is None and not state.you.can_manage


@pytest.mark.parametrize("who", ["anand", "stranger"])
def test_only_a_moderator_creates_a_room(client, w, who) -> None:
    resp = client.post(
        "/rooms",
        json={"circle_id": str(w.circle.id), "title": "t"},
        headers=_as(getattr(w, who)),
    )
    assert resp.status_code == 403
    assert w.db.query(Room).count() == 0


def test_a_site_moderator_may_create_in_a_circle_they_are_not_in(client, w) -> None:
    resp = client.post(
        "/rooms",
        json={"circle_id": str(w.circle.id), "title": "t"},
        headers=_as(w.site_mod),
    )
    assert resp.status_code == 201


@pytest.mark.parametrize("circle_id", [str(uuid.uuid4()), "not-a-uuid"])
def test_a_room_in_a_circle_that_does_not_exist_is_404(client, w, circle_id) -> None:
    resp = client.post("/rooms", json={"circle_id": circle_id, "title": "t"}, headers=_as(w.mod))
    assert resp.status_code == 404


@pytest.mark.parametrize("title", ["", "   ", "x" * 121])
def test_a_bad_title_is_refused(client, w, title) -> None:
    resp = client.post(
        "/rooms", json={"circle_id": str(w.circle.id), "title": title}, headers=_as(w.mod)
    )
    assert resp.status_code == 422


def test_a_stranger_is_told_the_room_does_not_exist(client, w, room_id) -> None:
    for method, path in (
        ("get", f"/rooms/{room_id}"),
        ("post", f"/rooms/{room_id}/queue"),
        ("delete", f"/rooms/{room_id}/queue"),
        ("post", f"/rooms/{room_id}/next"),
        ("post", f"/rooms/{room_id}/close"),
        ("get", "/rooms/not-a-uuid"),
        ("get", f"/rooms/{uuid.uuid4()}"),
    ):
        resp = getattr(client, method)(path, headers=_as(w.stranger))
        assert resp.status_code == 404, (method, path)


def test_the_list_is_the_open_rooms_of_my_circles(client, w, db_session, room_id) -> None:
    other = Circle(name="Elsewhere", kind="group", created_by=w.mod.id)
    db_session.add(other)
    db_session.commit()
    db_session.add(Membership(circle_id=other.id, user_id=w.mod.id, role="moderator"))
    db_session.commit()
    client.post(
        "/rooms", json={"circle_id": str(other.id), "title": "Elsewhere"}, headers=_as(w.mod)
    )

    mine = RoomList.model_validate(client.get("/rooms", headers=_as(w.anand)).json()).items
    assert [r.id for r in mine] == [room_id]
    assert client.get("/rooms", headers=_as(w.stranger)).json()["items"] == []
    both = client.get("/rooms", headers=_as(w.site_mod)).json()["items"]
    assert len(both) == 2

    only = client.get(f"/rooms?circle_id={other.id}", headers=_as(w.mod)).json()["items"]
    assert [r["title"] for r in only] == ["Elsewhere"]

    client.post(f"/rooms/{room_id}/close", headers=_as(w.mod))
    assert client.get("/rooms", headers=_as(w.anand)).json()["items"] == []


# --- the queue ----------------------------------------------------------------------------------------------


def test_raising_a_hand_joins_in_order_and_a_second_tap_changes_nothing(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    again = _raise(client, room_id, w.anand)
    assert [(e.name, e.position) for e in again.queue] == [("Anand", 1), ("Bhavya", 2)]
    assert again.you.state is QueueState.WAITING and again.you.position == 1
    assert again.room.waiting_count == 2
    assert w.db.query(LeadQueueEntry).count() == 2


def test_a_position_is_never_reused(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    client.post(f"/rooms/{room_id}/queue/{w.anand.id}/skip", headers=_as(w.mod))
    _raise(client, room_id, w.chitra)
    positions = [e.position for e in w.db.query(LeadQueueEntry).order_by(LeadQueueEntry.position)]
    assert positions == sorted(set(positions)) == [1, 2, 3]
    queue = _state(client, room_id, w.chitra).queue
    assert [(e.name, e.position) for e in queue] == [("Bhavya", 1), ("Chitra", 2)]


def test_the_database_refuses_two_leads_in_one_room(client, w, room_id, db_session) -> None:
    rid = uuid.UUID(room_id)
    db_session.add(LeadQueueEntry(room_id=rid, user_id=w.anand.id, position=1, state="leading"))
    db_session.commit()
    db_session.add(LeadQueueEntry(room_id=rid, user_id=w.bhavya.id, position=2, state="leading"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_the_database_refuses_a_person_in_the_queue_twice(client, w, room_id, db_session) -> None:
    rid = uuid.UUID(room_id)
    db_session.add(LeadQueueEntry(room_id=rid, user_id=w.anand.id, position=1))
    db_session.commit()
    db_session.add(LeadQueueEntry(room_id=rid, user_id=w.anand.id, position=2))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# --- the floor ------------------------------------------------------------------------------------------------------


def test_with_nobody_leading_only_the_first_in_line_or_a_moderator_may_take_the_floor(
    client, w, room_id
) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    assert _state(client, room_id, w.anand).you.can_take_floor is True
    assert _state(client, room_id, w.bhavya).you.can_take_floor is False
    assert _state(client, room_id, w.mod).you.can_take_floor is True
    assert _state(client, room_id, w.chitra).you.can_take_floor is False

    assert _next(client, room_id, w.bhavya).status_code == 403
    assert _lead_id(client, room_id, w.anand) is None
    taken = RoomState.model_validate(_next(client, room_id, w.anand).json())
    assert taken.lead and taken.lead.name == "Anand" and taken.you.is_lead
    assert [e.name for e in taken.queue] == ["Bhavya"]


def test_the_handoff_is_one_request_and_the_old_lead_is_done(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    _next(client, room_id, w.anand)

    assert _next(client, room_id, w.bhavya).status_code == 403  # cannot grab it from the lead
    handed = RoomState.model_validate(_next(client, room_id, w.anand).json())
    assert handed.lead and handed.lead.name == "Bhavya" and handed.queue == []
    after = _state(client, room_id, w.anand)
    assert after.you.is_lead is False and after.you.state is QueueState.DONE


def test_a_moderator_may_hand_over_at_any_time(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    _next(client, room_id, w.anand)
    handed = RoomState.model_validate(_next(client, room_id, w.mod).json())
    assert handed.lead and handed.lead.name == "Bhavya"


def test_the_lead_stepping_down_with_nobody_waiting_leaves_the_floor_empty(
    client, w, room_id
) -> None:
    _raise(client, room_id, w.anand)
    _next(client, room_id, w.anand)
    stepped = RoomState.model_validate(_next(client, room_id, w.anand).json())
    assert stepped.lead is None
    assert _next(client, room_id, w.mod).status_code == 409  # nothing to hand over


def test_the_lead_leaving_is_a_handoff_and_a_waiting_person_leaving_just_leaves(
    client, w, room_id
) -> None:
    for person in (w.anand, w.bhavya, w.chitra):
        _raise(client, room_id, person)
    _next(client, room_id, w.anand)

    left = RoomState.model_validate(
        client.delete(f"/rooms/{room_id}/queue", headers=_as(w.bhavya)).json()
    )
    assert [e.name for e in left.queue] == ["Chitra"] and left.lead.name == "Anand"

    out = RoomState.model_validate(
        client.delete(f"/rooms/{room_id}/queue", headers=_as(w.anand)).json()
    )
    assert out.lead and out.lead.name == "Chitra"


def test_a_person_may_raise_a_hand_again_after_a_turn(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    _next(client, room_id, w.anand)
    _next(client, room_id, w.anand)
    again = _raise(client, room_id, w.anand)
    assert again.you.state is QueueState.WAITING and again.you.position == 1


def test_only_a_moderator_skips_someone_and_only_someone_waiting(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    skip = f"/rooms/{room_id}/queue/{w.bhavya.id}/skip"
    assert client.post(skip, headers=_as(w.anand)).status_code == 403
    assert (
        client.post(f"/rooms/{room_id}/queue/not-a-uuid/skip", headers=_as(w.mod)).status_code
        == 404
    )
    assert (
        client.post(f"/rooms/{room_id}/queue/{w.chitra.id}/skip", headers=_as(w.mod)).status_code
        == 404
    )

    skipped = RoomState.model_validate(client.post(skip, headers=_as(w.mod)).json())
    assert [(e.name, e.position) for e in skipped.queue] == [("Anand", 1)]
    assert _state(client, room_id, w.bhavya).you.state is QueueState.REMOVED


def test_exactly_one_person_leads_whatever_happens(client, w, room_id) -> None:
    for person in (w.anand, w.bhavya, w.chitra):
        _raise(client, room_id, person)
    for who in (w.anand, w.mod, w.bhavya, w.chitra):
        _next(client, room_id, who)
        leaders = w.db.query(LeadQueueEntry).filter_by(state="leading").count()
        assert leaders <= 1


def test_two_hand_overs_at_once_are_serialised_by_the_room_lock(engine, client, w, room_id) -> None:
    """A second request that wants to change the room waits for the first to finish."""
    rid = uuid.UUID(room_id)
    first = Session(engine)
    assert lock_room(first, rid) is not None  # the first request holds the lock

    got_lock = threading.Event()

    def second_request() -> None:
        with Session(engine) as second:
            lock_room(second, rid)
            got_lock.set()

    thread = threading.Thread(target=second_request)
    thread.start()
    assert not got_lock.wait(timeout=0.7), "the second request must wait for the first"
    first.commit()
    first.close()
    assert got_lock.wait(timeout=5), "...and proceed once the first has finished"
    thread.join(timeout=5)


# --- closing ------------------------------------------------------------------------------------------------------


def test_only_a_moderator_closes_a_room_and_a_closed_room_does_nothing(
    client, w, room_id, lk
) -> None:
    _raise(client, room_id, w.anand)
    _next(client, room_id, w.anand)
    assert client.post(f"/rooms/{room_id}/close", headers=_as(w.anand)).status_code == 403

    closed = RoomState.model_validate(
        client.post(f"/rooms/{room_id}/close", headers=_as(w.mod)).json()
    )
    assert closed.room.status is RoomStatus.CLOSED and closed.room.closed_at is not None
    assert closed.lead is None  # nobody leads a closed room
    assert ("delete_room", f"room-{room_id}") in lk.calls  # everyone is disconnected

    assert client.post(f"/rooms/{room_id}/close", headers=_as(w.mod)).status_code == 200
    for path in ("queue", "token", "next"):
        assert client.post(f"/rooms/{room_id}/{path}", headers=_as(w.anand)).status_code == 409


def test_a_room_that_cannot_be_closed_in_livekit_stays_open(client, w, room_id, lk) -> None:
    lk.fail_delete = True
    resp = client.post(f"/rooms/{room_id}/close", headers=_as(w.mod))
    assert resp.status_code == 502
    assert _state(client, room_id, w.anand).room.status is RoomStatus.OPEN


# --- the token: the server decides who may speak ---------------------------------------------------------


def _claims(token: str) -> dict:
    return jwt.decode(
        token, SECRET, algorithms=["HS256"], options={"verify_aud": False}, issuer=API_KEY
    )


def test_without_livekit_set_up_the_token_route_says_so(client, w, room_id) -> None:
    resp = client.post(f"/rooms/{room_id}/token", headers=_as(w.anand))
    assert resp.status_code == 503


def test_a_listener_token_cannot_publish_and_a_lead_token_can(client, w, room_id, lk) -> None:
    _raise(client, room_id, w.anand)
    listener = RoomTokenOut.model_validate(
        client.post(f"/rooms/{room_id}/token", headers=_as(w.bhavya)).json()
    )
    waiting = RoomTokenOut.model_validate(
        client.post(f"/rooms/{room_id}/token", headers=_as(w.anand)).json()
    )
    _next(client, room_id, w.anand)
    lead = RoomTokenOut.model_validate(
        client.post(f"/rooms/{room_id}/token", headers=_as(w.anand)).json()
    )

    assert listener.can_publish is False and waiting.can_publish is False
    assert lead.can_publish is True
    assert _claims(listener.token)["video"]["canPublish"] is False
    assert _claims(waiting.token)["video"]["canPublish"] is False
    assert _claims(lead.token)["video"]["canPublish"] is True
    # The lead's token is short-lived: a hand-over cannot reach a token already issued, and
    # LiveKit re-applies its grants on every join (review of #151).
    lead_claims = _claims(lead.token)
    assert lead_claims["exp"] - lead_claims["nbf"] <= 120
    assert _claims(listener.token)["exp"] - _claims(listener.token)["nbf"] == 3600


def test_a_token_is_scoped_to_one_room_one_person_and_cannot_send_data(
    client, w, room_id, lk
) -> None:
    token = RoomTokenOut.model_validate(
        client.post(f"/rooms/{room_id}/token", headers=_as(w.bhavya)).json()
    )
    claims = _claims(token.token)
    assert token.url == "wss://example.test/livekit"
    assert token.identity == str(w.bhavya.id) == claims["sub"]
    assert claims["name"] == "Bhavya" and claims["iss"] == API_KEY
    assert claims["video"] == {
        "room": f"room-{room_id}",
        "roomJoin": True,
        "canSubscribe": True,
        "canPublish": False,
        "canPublishData": False,
    }
    assert claims["exp"] > claims["nbf"]
    assert token.expires_at.timestamp() == pytest.approx(claims["exp"], abs=2)
    assert SECRET not in token.token  # a JWT carries a signature, never the secret
    assert ("create_room", f"room-{room_id}") in lk.calls  # the 50-person limit is applied


def test_a_stranger_gets_no_token(client, w, room_id, lk) -> None:
    assert client.post(f"/rooms/{room_id}/token", headers=_as(w.stranger)).status_code == 404
    assert lk.calls == []


def test_a_token_when_livekit_is_down_is_a_502_not_a_token(client, w, room_id, lk) -> None:
    def broken(name: str) -> None:
        raise LiveKitUnavailable("down")

    lk.create_room = broken
    assert client.post(f"/rooms/{room_id}/token", headers=_as(w.anand)).status_code == 502


# --- the hand-over changes the permission, in the right order ------------------------------------------


def test_a_handoff_silences_the_old_lead_before_it_allows_the_new_one(
    client, w, room_id, lk
) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    _next(client, room_id, w.anand)
    assert lk.publishes() == [("set_can_publish", f"room-{room_id}", str(w.anand.id), True)]

    lk.calls.clear()
    _next(client, room_id, w.anand)
    assert lk.publishes() == [
        ("set_can_publish", f"room-{room_id}", str(w.anand.id), False),
        ("set_can_publish", f"room-{room_id}", str(w.bhavya.id), True),
    ]


def test_the_lead_stepping_down_is_silenced(client, w, room_id, lk) -> None:
    _raise(client, room_id, w.anand)
    _next(client, room_id, w.anand)
    lk.calls.clear()
    _next(client, room_id, w.anand)
    assert lk.publishes() == [("set_can_publish", f"room-{room_id}", str(w.anand.id), False)]


def test_the_lead_leaving_and_a_moderator_handoff_also_change_the_permission(
    client, w, room_id, lk
) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    _raise(client, room_id, w.chitra)
    _next(client, room_id, w.anand)
    lk.calls.clear()
    client.delete(f"/rooms/{room_id}/queue", headers=_as(w.anand))
    assert [c[2:] for c in lk.publishes()] == [(str(w.anand.id), False), (str(w.bhavya.id), True)]
    lk.calls.clear()
    _next(client, room_id, w.mod)
    assert [c[2:] for c in lk.publishes()] == [(str(w.bhavya.id), False), (str(w.chitra.id), True)]


def test_a_lead_who_is_not_connected_yet_is_not_an_error(client, w, room_id, lk) -> None:
    lk.connected = {"somebody-else"}  # LiveKit answers "no such participant" for everyone here
    _raise(client, room_id, w.anand)
    assert _next(client, room_id, w.anand).status_code == 200
    assert _lead_id(client, room_id, w.mod) == str(w.anand.id)


def test_if_the_old_lead_cannot_be_silenced_nothing_changes(client, w, room_id, lk) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    _next(client, room_id, w.anand)
    lk.fail_on = {lk._publish_calls + 1}  # the very next call, silencing Anand, fails
    resp = _next(client, room_id, w.anand)
    assert resp.status_code == 502
    after = _state(client, room_id, w.mod)
    assert after.lead and after.lead.name == "Anand"  # still the lead, in the database
    assert [e.name for e in after.queue] == ["Bhavya"]  # still waiting
    assert w.db.query(LeadQueueEntry).filter_by(state="leading").count() == 1


def test_if_the_new_lead_cannot_be_allowed_the_old_one_is_restored_and_nothing_changes(
    client, w, room_id, lk
) -> None:
    _raise(client, room_id, w.anand)
    _raise(client, room_id, w.bhavya)
    _next(client, room_id, w.anand)
    lk.calls.clear()
    lk.fail_on = {lk._publish_calls + 2}  # silencing Anand works, allowing Bhavya fails
    resp = _next(client, room_id, w.anand)

    assert resp.status_code == 502
    assert [c[2:] for c in lk.publishes()] == [
        (str(w.anand.id), False),
        (str(w.bhavya.id), True),
        (str(w.anand.id), True),  # put back, so the audio matches the database again
    ]
    after = _state(client, room_id, w.mod)
    assert after.lead and after.lead.name == "Anand"
    assert [e.name for e in after.queue] == ["Bhavya"]


def test_without_livekit_the_queue_still_works(client, w, room_id) -> None:
    _raise(client, room_id, w.anand)
    assert _next(client, room_id, w.anand).status_code == 200
    assert _lead_id(client, room_id, w.mod) == str(w.anand.id)


# --- the join check (LiveKit's participant_joined webhook, app/livekit_webhook.py) ---------------

WEBHOOK = "/internal/livekit/webhook"


def _webhook(client, event: dict | bytes, *, secret=SECRET, issuer=API_KEY, sha=None, signed=True):
    """POST an event the way LiveKit does: the body, and an Authorization JWT signed with the API
    secret whose `sha256` claim is the base64 SHA-256 of the exact body."""
    body = event if isinstance(event, bytes) else json.dumps(event).encode()
    digest = sha or base64.b64encode(hashlib.sha256(body).digest()).decode()
    headers = {"Content-Type": "application/webhook+json"}
    if signed:
        headers["Authorization"] = jwt.encode(
            {"iss": issuer, "sha256": digest}, secret, algorithm="HS256"
        )
    return client.post(WEBHOOK, content=body, headers=headers)


def _joined(room_id: str, person) -> dict:
    return {
        "event": "participant_joined",
        "room": {"name": f"room-{room_id}"},
        "participant": {"identity": str(person.id), "name": person.name},
    }


def test_a_person_who_led_and_rejoins_with_their_old_token_is_silenced(
    client, w, room_id, lk
) -> None:
    _raise(client, room_id, w.anand)
    _next(client, room_id, w.anand)  # Anand leads
    _raise(client, room_id, w.bhavya)
    _next(client, room_id, w.anand)  # hand-over: Bhavya leads, Anand is done
    lk.calls.clear()

    assert _webhook(client, _joined(room_id, w.anand)).status_code == 200  # Anand rejoins

    assert lk.publishes() == [("set_can_publish", f"room-{room_id}", str(w.anand.id), False)]


def test_a_listener_who_joins_is_set_to_cannot_publish(client, w, room_id, lk) -> None:
    assert _webhook(client, _joined(room_id, w.chitra)).status_code == 200
    assert lk.publishes() == [("set_can_publish", f"room-{room_id}", str(w.chitra.id), False)]


def test_the_lead_who_joins_muted_is_allowed_to_speak(client, w, room_id, lk) -> None:
    # Made the lead just before connecting: their token said canPublish false.
    _raise(client, room_id, w.anand)
    _next(client, room_id, w.anand)
    lk.calls.clear()
    assert _webhook(client, _joined(room_id, w.anand)).status_code == 200
    assert lk.publishes() == [("set_can_publish", f"room-{room_id}", str(w.anand.id), True)]


@pytest.mark.parametrize(
    "event",
    [
        {"event": "participant_left", "room": {"name": "ROOM"}, "participant": {"identity": "x"}},
        {"event": "room_started", "room": {"name": "ROOM"}},
        {
            "event": "participant_joined",
            "room": {"name": "room-not-one-of-ours"},
            "participant": {"identity": "x"},
        },
        {"event": "participant_joined", "room": {"name": "ROOM"}},
    ],
)
def test_events_that_need_no_change_are_accepted_and_change_nothing(
    client, w, room_id, lk, event
) -> None:
    event = json.loads(json.dumps(event).replace("ROOM", f"room-{room_id}"))
    assert _webhook(client, event).status_code == 200
    assert lk.publishes() == []


def test_a_closed_room_is_left_alone(client, w, room_id, lk) -> None:
    assert client.post(f"/rooms/{room_id}/close", headers=_as(w.mod)).status_code == 200
    lk.calls.clear()
    assert _webhook(client, _joined(room_id, w.anand)).status_code == 200
    assert lk.publishes() == []


def test_a_webhook_without_LiveKits_signature_is_refused(client, w, room_id, lk) -> None:
    event = _joined(room_id, w.anand)
    body = json.dumps(event).encode()
    wrong_hash = base64.b64encode(hashlib.sha256(b"another body").digest()).decode()
    cases = {
        "no signature": _webhook(client, event, signed=False),
        "another secret": _webhook(
            client, event, secret="another-secret-of-at-least-thirty-two-bytes"
        ),
        "another issuer": _webhook(client, event, issuer="not-our-key"),
        "body does not match the signed hash": _webhook(client, body, sha=wrong_hash),
    }
    for name, resp in cases.items():
        assert resp.status_code == 401, name
    assert (
        client.post(WEBHOOK, content=body, headers={"Authorization": "garbage"}).status_code == 401
    )
    assert lk.publishes() == []


def test_a_malformed_signed_body_is_a_400(client, lk) -> None:
    assert _webhook(client, b"not json").status_code == 400


def test_when_livekit_cannot_be_told_the_check_is_a_502_and_nothing_is_kept(
    client, w, room_id, lk
) -> None:
    lk.fail_on = {1}
    assert _webhook(client, _joined(room_id, w.anand)).status_code == 502
    # The room lock was released: a hand-over still works straight afterwards.
    assert _raise(client, room_id, w.anand) is not None
    assert _next(client, room_id, w.anand).status_code == 200


def test_the_webhook_is_503_when_audio_is_not_set_up(client, monkeypatch) -> None:
    settings = get_settings()
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setattr(settings, name, None)
    assert client.post(WEBHOOK, content=b"{}").status_code == 503


def test_the_webhook_is_not_a_public_route() -> None:
    # Caddy forwards only the paths it lists; /internal/* is not one, so it cannot be reached from
    # outside. If someone adds a route for it, this fails and they have to mean it.
    from pathlib import Path

    caddyfile = Path(__file__).resolve().parents[3] / "infra" / "caddy" / "Caddyfile"
    assert "/internal" not in caddyfile.read_text(encoding="utf-8")
