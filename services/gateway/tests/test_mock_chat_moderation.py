"""The moderator console's routes in the mock chat gateway: what M4 builds the console
against (issue #65) until it runs on the real gateway. Same statuses, same rules as
services/gateway/app/moderation.py: only `held` messages are queued, only a moderator may
look, a stale `expected_event_id` is a 409, and every decision appends an event."""

import uuid

import contracts.chat.mock.app as mock_app
import pytest
from contracts.chat.moderation import (
    ModerationActorKind,
    ModerationEventsOut,
    ModerationQueueOut,
    ModerationReviewOut,
)
from fastapi.testclient import TestClient

MOD = {"X-Mock-Role": "moderator"}


@pytest.fixture
def client():
    # The mock's state is module-level; the queue is global, so start each test empty.
    mock_app._messages.clear()
    mock_app._message_seq.clear()
    mock_app._events.clear()
    with TestClient(mock_app.app) as test_client:
        yield test_client


def _send(client, text, *, author="mock-user-1", target=None):
    resp = client.post(
        "/messages",
        headers={"X-Mock-User-Id": author},
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": target or str(uuid.uuid4()),
            "kind": "text",
            "text": text,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _queue(client, **params) -> ModerationQueueOut:
    resp = client.get("/moderation/queue", headers=MOD, params=params)
    assert resp.status_code == 200, resp.text
    return ModerationQueueOut.model_validate(resp.json())


def _status_of(client, message_id):
    for history in mock_app._messages.values():
        for m in history:
            if m.id == message_id:
                return m.status.value
    raise AssertionError("message not stored")


def test_a_held_message_is_queued_with_the_classifiers_event(client) -> None:
    message_id = _send(client, "please hold this one")

    (item,) = _queue(client).items

    assert str(item.message_id) == message_id
    assert item.original_text == "please hold this one"
    assert item.event_count == 1
    assert item.latest_event.actor_kind is ModerationActorKind.CLASSIFIER
    assert item.latest_event.action.value == "HOLD"
    assert item.latest_event.actor_id is None and item.latest_event.confidence is not None
    # the side-by-side view needs both texts; the mock's pivot is visibly a stand-in
    assert item.pivot_text_en and item.pivot_text_en != item.original_text


def test_only_held_messages_are_queued_not_blocked_or_delivered_ones(client) -> None:
    _send(client, "hello there")
    _send(client, "this should be a block")

    assert _queue(client).items == []


def test_a_non_uuid_mock_identity_still_produces_valid_queue_items(client) -> None:
    # The mock's default user id is "mock-user-1"; the contract's ids are UUIDs.
    _send(client, "hold", author="mock-user-1", target="mock-user-2")
    first = _queue(client).items[0]
    _send(client, "hold again", author="mock-user-1", target="mock-user-2")

    assert first.author_id == _queue(client).items[1].author_id  # stable, not random


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/moderation/queue"),
        ("get", f"/moderation/messages/{uuid.uuid4()}/events"),
        ("post", f"/moderation/messages/{uuid.uuid4()}/release"),
        ("post", f"/moderation/messages/{uuid.uuid4()}/block"),
    ],
)
def test_every_route_refuses_a_caller_who_is_not_a_moderator(client, method, path) -> None:
    body = {"json": {}} if method == "post" else {}
    for headers in ({}, {"X-Mock-Role": "elder"}):
        assert getattr(client, method)(path, headers=headers, **body).status_code == 403
    assert getattr(client, method)(path, headers={"X-Mock-Role": "admin"}, **body).status_code != 403


def test_release_delivers_the_message_leaves_the_queue_and_appends_an_event(client) -> None:
    message_id = _send(client, "hold")
    (item,) = _queue(client).items

    resp = client.post(
        f"/moderation/messages/{message_id}/release",
        headers=MOD,
        json={"note": "fine on reading", "expected_event_id": str(item.latest_event.id)},
    )

    assert resp.status_code == 200, resp.text
    out = ModerationReviewOut.model_validate(resp.json())
    assert out.message_status == "delivered"
    assert out.event.actor_kind is ModerationActorKind.MODERATOR
    assert out.event.action.value == "ALLOW" and out.event.note == "fine on reading"
    assert out.event.actor_id is not None
    assert out.event.confidence is None and out.event.model_version is None
    assert out.notice_sent is False  # honest, as on the real gateway (OPEN_QUESTIONS #18)
    assert _status_of(client, message_id) == "delivered"
    assert _queue(client).items == []

    trail = ModerationEventsOut.model_validate(
        client.get(f"/moderation/messages/{message_id}/events", headers=MOD).json()
    )
    assert [e.actor_kind.value for e in trail.events] == ["classifier", "moderator"]  # oldest first


def test_block_blocks_the_message_and_appends_an_event(client) -> None:
    message_id = _send(client, "hold")

    resp = client.post(f"/moderation/messages/{message_id}/block", headers=MOD, json={})

    out = ModerationReviewOut.model_validate(resp.json())
    assert out.message_status == "blocked" and out.event.action.value == "BLOCK"
    assert _status_of(client, message_id) == "blocked"
    assert _queue(client).items == []


def test_a_release_may_reverse_a_block_but_a_block_needs_a_held_message(client) -> None:
    blocked_id = _send(client, "this should be a block")

    assert client.post(f"/moderation/messages/{blocked_id}/block", headers=MOD, json={}).status_code == 409
    assert client.post(f"/moderation/messages/{blocked_id}/release", headers=MOD, json={}).status_code == 200
    trail = client.get(f"/moderation/messages/{blocked_id}/events", headers=MOD).json()["events"]
    assert [e["actor_kind"] for e in trail] == ["classifier", "moderator"]


def test_a_stale_expected_event_id_is_a_conflict_and_changes_nothing(client) -> None:
    message_id = _send(client, "hold")

    resp = client.post(
        f"/moderation/messages/{message_id}/release",
        headers=MOD,
        json={"expected_event_id": str(uuid.uuid4())},
    )

    assert resp.status_code == 409
    assert _status_of(client, message_id) == "held"
    assert len(_queue(client).items) == 1


def test_deciding_a_message_that_is_not_waiting_is_a_conflict_and_an_unknown_one_a_404(client) -> None:
    delivered_id = _send(client, "hello")

    assert client.post(f"/moderation/messages/{delivered_id}/release", headers=MOD, json={}).status_code == 409
    for action in ("release", "block"):
        unknown = client.post(f"/moderation/messages/{uuid.uuid4()}/{action}", headers=MOD, json={})
        assert unknown.status_code == 404
    assert client.get(f"/moderation/messages/{uuid.uuid4()}/events", headers=MOD).status_code == 404


def test_the_queue_pages_by_cursor_oldest_first(client) -> None:
    ids = [_send(client, f"hold {n}") for n in range(3)]

    first = _queue(client, limit=2)
    second = _queue(client, limit=2, cursor=first.next_cursor)

    assert [str(i.message_id) for i in first.items] == ids[:2]
    assert first.next_cursor is not None
    assert [str(i.message_id) for i in second.items] == ids[2:]
    assert second.next_cursor is None
    assert (
        client.get("/moderation/queue", headers=MOD, params={"cursor": "not-a-cursor"}).status_code
        == 422
    )
