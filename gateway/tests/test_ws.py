"""Gateway WebSocket relay: connect, auth, broadcast, and the
backbone-unavailable degraded path.

Uses a FakeBackbone (same shape as test_circles.py's) rather than a real
backbone or Postgres/Matrix -- ws.py's own logic (registry, broadcast,
accept-then-reject auth, self-healing default circle) is what's under
test here, not circles delivery semantics, which are already covered in
backbone/spike-matrix-a/circle_service/tests/.
"""

from datetime import datetime, timezone
from typing import List, Optional

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import circles
import ws
from auth import issue_token
from interfaces import BackboneUnavailable, CircleBackbone, CircleMessage
from main import app


class FakeBackbone(CircleBackbone):
    def __init__(self, start_unavailable: bool = False):
        self.circles = {}
        self.members = {}
        self.messages = {}
        self._next_id = 1
        self._unavailable = start_unavailable

    def _check(self):
        if self._unavailable:
            raise BackboneUnavailable("simulated: backbone not reachable")

    async def create_circle(self, name: str) -> str:
        self._check()
        circle_id = str(self._next_id)
        self._next_id += 1
        self.circles[circle_id] = name
        self.members[circle_id] = []
        self.messages[circle_id] = []
        return circle_id

    async def add_member(self, circle_id: str, user_id: str) -> None:
        self._check()
        if user_id not in self.members[circle_id]:
            self.members[circle_id].append(user_id)

    async def remove_member(self, circle_id: str, user_id: str) -> None:
        self._check()
        if user_id in self.members[circle_id]:
            self.members[circle_id].remove(user_id)

    async def list_members(self, circle_id: str) -> List[str]:
        self._check()
        return list(self.members[circle_id])

    async def post_announcement(self, circle_id: str, sender_id: str, body: str) -> str:
        self._check()
        message_id = str(self._next_id)
        self._next_id += 1
        self.messages[circle_id].append(
            CircleMessage(message_id, circle_id, sender_id, body, datetime.now(timezone.utc))
        )
        return message_id

    async def list_messages(
        self, circle_id: str, limit: int = 50, before: Optional[str] = None
    ) -> List[CircleMessage]:
        self._check()
        return list(reversed(self.messages[circle_id]))[:limit]


@pytest.fixture(autouse=True)
def reset_ws_state():
    """ws.py's default-circle id and registry are module-level, so tests
    would otherwise leak state into each other -- a circle created by an
    earlier test would still look "ready" to a later one using a fresh
    FakeBackbone that's never heard of it."""
    ws._default_circle_id = None
    ws.registry = ws.ConnectionRegistry()
    yield
    ws._default_circle_id = None
    ws.registry = ws.ConnectionRegistry()


def test_connect_without_token_is_rejected():
    circles.set_backbone(FakeBackbone())
    with TestClient(app) as client:
        with pytest.raises(Exception):
            # No token query param at all -- FastAPI itself should
            # reject this as a missing required parameter before ws.py
            # ever runs.
            with client.websocket_connect("/ws"):
                pass


def test_connect_with_invalid_token_is_rejected_with_code_4401():
    # Asserts the actual close code, not just "some exception happened" --
    # a generic pytest.raises(Exception) here would pass identically
    # whether the server sent a real 4401 close frame or collapsed to an
    # ambiguous rejection, which is exactly the gap that let this
    # accept()-before-close() ordering bug (see ws.py's own comment on it)
    # go unnoticed: the client's own reconnect JS checks for this precise
    # code to know "stop retrying, this was an auth rejection" (see
    # clients/elder-app/elder_app/gateway_ws_proof.py), so the code value
    # itself is the thing under test, not merely "did it disconnect."
    #
    # accept() THEN close() means the connection itself opens successfully
    # (websocket_connect() below does not raise) -- the close only
    # surfaces once something tries to interact with the now-closed
    # socket, which is why the assertion is on receive_text(), not on
    # entering the context manager. Confirmed by first writing this test
    # the more obvious way (pytest.raises around websocket_connect
    # itself) and watching it fail with "DID NOT RAISE" -- exactly the
    # TestClient-vs-real-browser discrepancy this whole fix is about.
    circles.set_backbone(FakeBackbone())
    with TestClient(app) as client:
        with client.websocket_connect("/ws?token=garbage-not-a-real-token") as websocket:
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_text()
        assert exc_info.value.code == 4401


def test_connect_with_valid_token_receives_history():
    circles.set_backbone(FakeBackbone())
    token, user_id = issue_token("alice")
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={token}") as ws_conn:
            first = ws_conn.receive_json()
            assert first["type"] == "history"
            assert first["messages"] == []


def test_two_clients_broadcast_to_each_other():
    circles.set_backbone(FakeBackbone())
    token_a, user_a = issue_token("alice")
    token_b, user_b = issue_token("bob")

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={token_a}") as ws_a, \
             client.websocket_connect(f"/ws?token={token_b}") as ws_b:
            ws_a.receive_json()  # history
            ws_b.receive_json()  # history

            ws_a.send_json({"body": "hello from alice"})

            msg_a = ws_a.receive_json()
            msg_b = ws_b.receive_json()
            assert msg_a == {"type": "message", "sender_id": "alice", "body": "hello from alice"}
            assert msg_b == msg_a  # both connected clients get the same broadcast


def test_message_is_actually_persisted_through_the_backbone():
    backbone = FakeBackbone()
    circles.set_backbone(backbone)
    token, user_id = issue_token("alice")

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={token}") as ws_conn:
            ws_conn.receive_json()  # history
            ws_conn.send_json({"body": "durable message"})
            ws_conn.receive_json()  # the broadcast back to sender

    # After disconnect, the message is retrievable via the same fake
    # store's normal history mechanism -- proves the WS path writes
    # through backbone.post_announcement, not just relays live.
    circle_id = ws._default_circle_id
    assert circle_id is not None
    bodies = [m.body for m in backbone.messages[circle_id]]
    assert "durable message" in bodies


def test_backbone_unavailable_at_connect_still_allows_live_relay():
    circles.set_backbone(FakeBackbone(start_unavailable=True))
    token_a, _ = issue_token("alice")
    token_b, _ = issue_token("bob")

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={token_a}") as ws_a, \
             client.websocket_connect(f"/ws?token={token_b}") as ws_b:
            history_a = ws_a.receive_json()
            assert history_a["messages"] == []
            assert "warning" in history_a  # told, not silently degraded

            ws_b.receive_json()  # history

            ws_a.send_json({"body": "relayed but not saved"})
            msg_b = ws_b.receive_json()
            assert msg_b["body"] == "relayed but not saved"


def test_empty_message_is_ignored_not_broadcast():
    circles.set_backbone(FakeBackbone())
    token, _ = issue_token("alice")
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws?token={token}") as ws_conn:
            ws_conn.receive_json()  # history
            ws_conn.send_json({"body": "   "})
            ws_conn.send_json({"body": "real one"})
            msg = ws_conn.receive_json()
            assert msg["body"] == "real one"  # the blank never arrived first
