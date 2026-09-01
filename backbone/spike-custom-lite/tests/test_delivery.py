"""Behaviours 1-3: online delivery, offline queueing + ordered backlog
drain on reconnect, and ordering under a rapid burst.

Runs entirely in-process via Starlette's TestClient, which triggers
app.py's lifespan (so the real background dispatcher task is actually
running, polling real Postgres) without needing a separate server process
or `docker compose --profile spike up`.
"""

from app import app
from starlette.testclient import TestClient


def _send(client, **kwargs):
    resp = client.post("/send", json=kwargs)
    assert resp.status_code == 200, resp.text
    return resp.json()["message_id"]


def test_online_delivery(spike_clean_db):
    """Behaviour 1: recipient already connected, message arrives."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws?user_id=bob") as ws:
            _send(
                client,
                conversation_id="c1",
                sender_id="alice",
                recipient_ids=["bob"],
                body="hello bob",
            )
            data = ws.receive_json()
            assert data["body"] == "hello bob"
            assert data["sender_id"] == "alice"
            assert data["conversation_id"] == "c1"


def test_offline_queueing_then_reconnect_drains_in_order(spike_clean_db):
    """Behaviour 2: recipient disconnected (never connected yet, in this
    case) when messages are sent. They stay pending; on connect, the
    backlog drains in original send order."""
    with TestClient(app) as client:
        for i in range(3):
            _send(
                client,
                conversation_id="c1",
                sender_id="alice",
                recipient_ids=["bob"],
                body=f"msg-{i}",
            )

        # Give the dispatcher a few poll cycles worth of time to prove
        # this isn't passing by accident because we happened to connect
        # before it ever ran.
        import time

        time.sleep(0.6)

        with client.websocket_connect("/ws?user_id=bob") as ws:
            received = [ws.receive_json()["body"] for _ in range(3)]

        assert received == ["msg-0", "msg-1", "msg-2"]


def test_ordering_20_rapid_messages(spike_clean_db):
    """Behaviour 3: 20 messages sent back-to-back arrive in send order.
    Guaranteed by construction (claim_batch's ORDER BY id, applied
    consistently across however many poll cycles the delivery spans) --
    this test is here to prove that in practice, not just in theory."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws?user_id=carol") as ws:
            for i in range(20):
                _send(
                    client,
                    conversation_id="c1",
                    sender_id="alice",
                    recipient_ids=["carol"],
                    body=f"m{i}",
                )
            received = [ws.receive_json()["body"] for _ in range(20)]

        assert received == [f"m{i}" for i in range(20)]


def test_two_recipients_each_get_their_own_copy(spike_clean_db):
    """Not one of the five required behaviours, but worth a cheap check:
    one send with two recipients writes two outbox rows (one per
    recipient), and both actually receive it."""
    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws?user_id=bob") as ws_bob,
            client.websocket_connect("/ws?user_id=carol") as ws_carol,
        ):
            _send(
                client,
                conversation_id="c1",
                sender_id="alice",
                recipient_ids=["bob", "carol"],
                body="hi both",
            )
            assert ws_bob.receive_json()["body"] == "hi both"
            assert ws_carol.receive_json()["body"] == "hi both"
