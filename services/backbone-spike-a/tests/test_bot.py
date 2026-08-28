"""
Tests for the Spike A Application Service bot.

These hit the FastAPI app in-process (no running server, no live Conduit
needed) to prove the AS transaction/query contract independent of the
end-to-end Matrix wiring, which is verified manually in Step 3.
"""

import os

# Set before importing app.bot so the module's module-level config picks
# these up instead of whatever (or nothing) is in a real .env.
os.environ.setdefault("HS_TOKEN", "test_hs_token")
os.environ.setdefault("AS_TOKEN", "test_as_token")
os.environ.setdefault("USER_NAMESPACE_REGEX", r"@spike_.*:localhost")
os.environ.setdefault("ROOM_ALIAS_NAMESPACE_REGEX", r"#spike_.*:localhost")

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bot import _fully_qualified_room_id, app, received_events

HS_TOKEN = os.environ["HS_TOKEN"]


@pytest.fixture
def client():
    received_events.clear()
    return TestClient(app)


def _sample_event(
    event_id="$event1:localhost",
    room_id="!room1:localhost",
    sender="@spike_alice:localhost",
    body="hello",
):
    return {
        "type": "m.room.message",
        "event_id": event_id,
        "room_id": room_id,
        "sender": sender,
        "origin_server_ts": 1234567890,
        "content": {"msgtype": "m.text", "body": body},
    }


def test_transactions_endpoint_accepts_valid_token(client):
    event = _sample_event()

    resp = client.put(
        "/_matrix/app/v1/transactions/txn1",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
        json={"events": [event]},
    )

    assert resp.status_code == 200
    # Matrix spec: homeserver expects an empty JSON object back.
    assert resp.json() == {}


def test_transactions_endpoint_rejects_bad_token(client):
    event = _sample_event()

    wrong_token = client.put(
        "/_matrix/app/v1/transactions/txn2",
        headers={"Authorization": "Bearer wrong_token"},
        json={"events": [event]},
    )
    assert wrong_token.status_code == 401

    missing_token = client.put(
        "/_matrix/app/v1/transactions/txn3",
        json={"events": [event]},
    )
    assert missing_token.status_code == 401

    # Bad requests must not leak into the store.
    assert received_events == []


def test_received_events_endpoint_returns_stored_events(client):
    event = _sample_event(
        event_id="$event42:localhost",
        room_id="!spikeroom:localhost",
        sender="@spike_bob:localhost",
        body="namaste",
    )

    put_resp = client.put(
        "/_matrix/app/v1/transactions/txn4",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
        json={"events": [event]},
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/received")
    assert get_resp.status_code == 200

    events = get_resp.json()
    matches = [e for e in events if e["event_id"] == "$event42:localhost"]
    assert len(matches) == 1

    stored = matches[0]
    assert stored["room_id"] == "!spikeroom:localhost"
    assert stored["sender"] == "@spike_bob:localhost"
    assert stored["content"]["body"] == "namaste"


def test_invite_join_failure_does_not_break_transaction_ack(client, monkeypatch):
    # A join failure (e.g. a homeserver-side bug) is a best-effort side
    # action failing -- it must not stop us acknowledging the event itself,
    # or the homeserver will retry the transaction forever.
    from app import bot as bot_module

    async def _raise_join_error(room_id):
        request = httpx.Request("POST", "http://example.invalid")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("join failed", request=request, response=response)

    monkeypatch.setattr(bot_module, "_join_room_as_bot", _raise_join_error)

    invite_event = {
        "type": "m.room.member",
        "state_key": bot_module.BOT_USER_ID,
        "sender": "@shyam:localhost",
        "room_id": "!someroom",
        "event_id": "$invite1",
        "content": {"membership": "invite"},
    }

    resp = client.put(
        "/_matrix/app/v1/transactions/txn-invite-fail",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
        json={"events": [invite_event]},
    )

    assert resp.status_code == 200
    assert resp.json() == {}
    assert any(e["event_id"] == "$invite1" for e in received_events)


def test_user_and_room_query_endpoints_respond(client):
    # Namespace claims @spike_*:localhost / #spike_*:localhost (see
    # registration.yaml). Anything outside that namespace is not ours, and
    # per Matrix spec the homeserver expects 404 for a user/room this AS
    # does not own.
    claimed_user = client.get(
        "/_matrix/app/v1/users/@spike_alice:localhost",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
    )
    assert claimed_user.status_code == 200

    unclaimed_user = client.get(
        "/_matrix/app/v1/users/@someone_else:localhost",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
    )
    assert unclaimed_user.status_code == 404

    claimed_room = client.get(
        "/_matrix/app/v1/rooms/%23spike_room:localhost",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
    )
    assert claimed_room.status_code == 200

    unclaimed_room = client.get(
        "/_matrix/app/v1/rooms/%23general:localhost",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
    )
    assert unclaimed_room.status_code == 404


def test_fully_qualified_room_id_appends_domain_when_missing():
    # Conduit's transaction payloads have been observed to omit the
    # server-name suffix; the join call needs it, so we add it back.
    assert (
        _fully_qualified_room_id("!abc123") == "!abc123:localhost"
    )


def test_fully_qualified_room_id_leaves_already_qualified_id_untouched():
    assert (
        _fully_qualified_room_id("!abc123:localhost") == "!abc123:localhost"
    )


def test_keys_query_proxy_responds_empty_for_valid_token(client):
    resp = client.post(
        "/_matrix/app/unstable/org.matrix.msc3984/keys/query",
        headers={"Authorization": f"Bearer {HS_TOKEN}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"device_keys": {}, "master_keys": {}, "self_signing_keys": {}}


def test_keys_query_proxy_rejects_bad_token(client):
    resp = client.post(
        "/_matrix/app/unstable/org.matrix.msc3984/keys/query",
        headers={"Authorization": "Bearer wrong_token"},
    )
    assert resp.status_code == 401
