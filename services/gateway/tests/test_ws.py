import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_ws_echo(client):
    with client.websocket_connect("/ws?token=faketoken") as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "hello"


def test_ws_rejects_missing_token(client):
    # The server now accepts the handshake, then immediately sends a real WS
    # close frame with code 1008 and this reason — that frame is what a real
    # browser actually receives in its onclose handler. So the handshake
    # itself succeeds (the `with` block enters cleanly) and the rejection
    # surfaces on the first receive, exactly as a browser would see it.
    #
    # Before this, the server closed pre-accept, which TestClient reported as
    # 1008 too, but that number was read straight off an internal ASGI message
    # a real client never sees — uvicorn collapses a pre-accept close to a
    # bare HTTP 403 and discards the code, so real browsers observed 1006, not
    # 1008. See docs/DECISIONS.md. TestClient and a browser now agree by
    # construction, not by coincidence.
    with client.websocket_connect("/ws") as ws, pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_text()
    assert exc_info.value.code == 1008
    assert exc_info.value.reason == "missing_or_invalid_token"


def test_ws_handles_disconnect(client):
    # The client-side context manager closes the socket on exit, simulating a
    # client that vanishes mid-session (phone sleeps, lift loses signal, etc).
    # If the server's receive loop let WebSocketDisconnect propagate as an
    # unhandled error, FastAPI's TestClient would surface that as an exception
    # here on __exit__. Reaching the end of this test without one *is* the
    # assertion that the server treated the disconnect as normal control flow.
    with client.websocket_connect("/ws?token=faketoken") as ws:
        ws.send_text("hello")
        assert ws.receive_text() == "hello"
