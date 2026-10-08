"""The sender's notice in the mock chat gateway (OPEN_QUESTIONS #18), so M1's sender screen can be
built against it until the real gateway is in use. Same rules as services/gateway/app/db/
moderation.py::notices_for_author, which tests/test_sender_notice.py pins on the real one:

  * the AUTHOR sees `moderation_notice` (+ language) on a nudged/held/blocked message, over HTTP and
    over the WebSocket's sync.batch; nobody else does;
  * the notice is the one on the LATEST event, so a moderator's ruling leaves none (no stale reason);
  * no classifier detail reaches the sender.

The wording is a canned placeholder, NOT policy: the words a sender is shown are M4's to write.
"""

import uuid

import contracts.chat.mock.app as mock_app
import pytest
from fastapi.testclient import TestClient

MOD = {"X-Mock-Role": "moderator"}
AUTHOR, OTHER = "author-1", "other-1"
SECRET_WORDS = ("rationale", "Mock classifier", "D_DISPUTATIONAL", "E_HARMFUL", "0.9")


@pytest.fixture
def client():
    mock_app._messages.clear()
    mock_app._message_seq.clear()
    mock_app._events.clear()
    with TestClient(mock_app.app) as test_client:
        yield test_client


def _send(client, text, *, author=AUTHOR, target="room-1"):
    resp = client.post(
        "/messages",
        headers={"X-Mock-User-Id": author},
        json={
            "client_msg_id": str(uuid.uuid4()),
            "target_type": "user",
            "target_id": target,
            "kind": "text",
            "text": text,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _read(client, viewer, target="room-1"):
    resp = client.get(
        "/messages",
        headers={"X-Mock-User-Id": viewer},
        params={"target_type": "user", "target_id": target},
    )
    assert resp.status_code == 200, resp.text
    return resp


@pytest.mark.parametrize("keyword", ["hold", "block"])
def test_the_author_sees_the_notice_on_a_held_or_blocked_message(client, keyword):
    _send(client, keyword)

    (message,) = _read(client, AUTHOR).json()["messages"]

    assert message["moderation_notice"]
    assert message["moderation_notice_language"] == "te"


@pytest.mark.parametrize("keyword", ["hold", "block"])
def test_nobody_else_sees_it(client, keyword):
    _send(client, keyword)

    response = _read(client, OTHER)

    (message,) = response.json()["messages"]
    assert message["moderation_notice"] is None
    assert message["moderation_notice_language"] is None


def test_a_normal_message_has_no_notice_for_anyone(client):
    _send(client, "good morning")

    for viewer in (AUTHOR, OTHER):
        (message,) = _read(client, viewer).json()["messages"]
        assert message["moderation_notice"] is None


def test_the_leak_check_is_not_fooled_by_a_timestamp_that_happens_to_contain_0_9():
    """The first version searched the raw JSON for the substring "0.9" (the classifier's confidence).
    A timestamp whose seconds are 00.9xx contains it, so CI failed about one run in ten at random."""
    assert _classifier_leaks({"created_at": "2026-10-08T04:08:00.912Z"}) == []
    assert _classifier_leaks({"confidence": 0.9}) != []
    assert _classifier_leaks({"nested": [{"label": "D_DISPUTATIONAL"}]}) != []
    assert _classifier_leaks({"moderation_notice": "Please be kind."}) == []


def test_the_sender_is_shown_the_notice_and_nothing_of_the_classifier(client):
    _send(client, "block")

    text = _read(client, AUTHOR).text

    for secret in SECRET_WORDS:
        assert secret not in text, f"the sender must not be handed {secret!r}"


def test_a_moderator_ruling_leaves_no_stale_notice_on_the_senders_screen(client):
    message_id = _send(client, "hold")
    assert _read(client, AUTHOR).json()["messages"][0]["moderation_notice"]

    resp = client.post(f"/moderation/messages/{message_id}/release", headers=MOD, json={})
    assert resp.status_code == 200, resp.text

    (message,) = _read(client, AUTHOR).json()["messages"]
    assert message["status"] == "sent"
    assert message["moderation_notice"] is None


def test_websocket_sync_follows_the_same_rule(client):
    _send(client, "hold")

    def sync(user):
        with client.websocket_connect(f"/ws?user_id={user}") as ws:
            ws.send_json(
                {"type": "sync.request", "data": {"target_type": "user", "target_id": "room-1"}}
            )
            frame = ws.receive_json()
        assert frame["type"] == "sync.batch"
        (message,) = frame["data"]["messages"]
        return message

    assert sync(AUTHOR)["moderation_notice"]
    assert sync(OTHER)["moderation_notice"] is None
