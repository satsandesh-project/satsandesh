"""
Mock chat gateway tests — prioritized: the client developer builds against
contracts/chat/mock/app.py from today, so it must be runnable and validated
first. Mirrors services/ai/tests/test_mock_server.py.
"""

import pytest
from contracts.chat.envelope import SyncBatch
from contracts.chat.messages import AckOut
from contracts.chat.mock.app import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Entering as a context manager (not a bare TestClient(app)) so the
    # anyio portal + socketpair is set up once, up front, which is required
    # for the WebSocket tests below to behave reliably on this Windows
    # setup — see services/gateway/tests/test_health.py for the mechanism.
    with TestClient(app) as test_client:
        yield test_client


def test_post_messages_returns_a_valid_ack(client: TestClient) -> None:
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": "11111111-1111-1111-1111-111111111111",
            "target_type": "circle",
            "target_id": "circle-1",
            "kind": "text",
            "text": "Namaste, satsang starts at 6pm",
        },
    )
    assert resp.status_code == 200
    ack = AckOut.model_validate(resp.json())
    assert str(ack.client_msg_id) == "11111111-1111-1111-1111-111111111111"
    assert ack.status.value == "delivered"


def test_post_messages_with_block_keyword_is_blocked(client: TestClient) -> None:
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": "22222222-2222-2222-2222-222222222222",
            "target_type": "circle",
            "target_id": "circle-1",
            "kind": "text",
            "text": "please block this",
        },
    )
    ack = AckOut.model_validate(resp.json())
    assert ack.status.value == "blocked"


def test_voice_message_without_media_ref_is_rejected_with_422(client: TestClient) -> None:
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": "33333333-3333-3333-3333-333333333333",
            "target_type": "circle",
            "target_id": "circle-1",
            "kind": "voice",
        },
    )
    assert resp.status_code == 422


def test_voice_message_is_pending_until_transcribed(client: TestClient) -> None:
    resp = client.post(
        "/messages",
        json={
            "client_msg_id": "88888888-8888-8888-8888-888888888888",
            "target_type": "circle",
            "target_id": "circle-1",
            "kind": "voice",
            "media_ref": {"uri": "mock://audio/a.wav"},
        },
    )
    ack = AckOut.model_validate(resp.json())
    assert ack.status.value == "pending"


def test_get_messages_returns_previously_sent_message(client: TestClient) -> None:
    client.post(
        "/messages",
        json={
            "client_msg_id": "44444444-4444-4444-4444-444444444444",
            "target_type": "circle",
            "target_id": "circle-2",
            "kind": "text",
            "text": "hello circle-2",
        },
    )
    resp = client.get("/messages", params={"target_type": "circle", "target_id": "circle-2"})
    assert resp.status_code == 200
    batch = SyncBatch.model_validate(resp.json())
    assert len(batch.messages) == 1
    assert batch.messages[0].text == "hello circle-2"


def test_get_messages_since_excludes_already_seen_messages(client: TestClient) -> None:
    first = client.post(
        "/messages",
        json={
            "client_msg_id": "55555555-5555-5555-5555-555555555555",
            "target_type": "circle",
            "target_id": "circle-3",
            "kind": "text",
            "text": "first",
        },
    ).json()
    client.post(
        "/messages",
        json={
            "client_msg_id": "66666666-6666-6666-6666-666666666666",
            "target_type": "circle",
            "target_id": "circle-3",
            "kind": "text",
            "text": "second",
        },
    )
    resp = client.get(
        "/messages",
        params={"target_type": "circle", "target_id": "circle-3", "since": first["id"]},
    )
    batch = SyncBatch.model_validate(resp.json())
    assert len(batch.messages) == 1
    assert batch.messages[0].text == "second"


def test_create_and_list_circles(client: TestClient) -> None:
    created = client.post("/circles", json={"name": "Satsang Group"}).json()
    resp = client.get("/circles")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert "Satsang Group" in names
    assert created["created_by"] == "mock-user-1"


def test_add_member_to_unknown_circle_returns_404(client: TestClient) -> None:
    resp = client.post("/circles/does-not-exist/members", json={"user_id": "user-9"})
    assert resp.status_code == 404


def test_add_member_to_existing_circle(client: TestClient) -> None:
    circle = client.post("/circles", json={"name": "Bhajan Circle"}).json()
    resp = client.post(f"/circles/{circle['id']}/members", json={"user_id": "user-9"})
    assert resp.status_code == 200
    membership = resp.json()
    assert membership["user_id"] == "user-9"
    assert membership["role"] == "member"


def test_ws_message_send_gets_ack_then_new(client: TestClient) -> None:
    with client.websocket_connect("/ws?user_id=user-42") as ws:
        ws.send_json(
            {
                "type": "message.send",
                "data": {
                    "client_msg_id": "77777777-7777-7777-7777-777777777777",
                    "target_type": "user",
                    "target_id": "user-99",
                    "kind": "text",
                    "text": "hi over ws",
                },
            }
        )
        ack_frame = ws.receive_json()
        new_frame = ws.receive_json()
        assert ack_frame["type"] == "message.ack"
        assert new_frame["type"] == "message.new"
        assert new_frame["data"]["author_id"] == "user-42"


def test_ws_sync_request_returns_sync_batch(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {"type": "sync.request", "data": {"target_type": "circle", "target_id": "circle-1"}}
        )
        frame = ws.receive_json()
        assert frame["type"] == "sync.batch"


def test_ws_unknown_frame_type_gets_error_frame(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "message.ack", "data": {}})
        frame = ws.receive_json()
        assert frame["type"] == "error"
