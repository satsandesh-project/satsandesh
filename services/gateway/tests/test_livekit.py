"""app/livekit.py: the access tokens and the admin calls, without a LiveKit server.

The admin client is driven through httpx's MockTransport, so each test sees exactly what would go
over the wire: the path, the admin token, the JSON body. What is checked is what LiveKit's API
needs to accept the call, and what the gateway relies on: a 404 for a participant who is not
connected is "nothing to change" (not an error), any other failure raises, and no secret is ever
in a body, a URL or an error.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from jose import jwt

from app import livekit
from app.config import get_settings
from app.livekit import LiveKitAdmin, LiveKitUnavailable

SECRET = "a-livekit-api-secret-of-at-least-thirty-two-bytes"
API_KEY = "devkey"


@pytest.fixture
def configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SECRET)
    monkeypatch.setattr(settings, "LIVEKIT_PUBLIC_URL", "wss://example.test/livekit")
    monkeypatch.setattr(settings, "LIVEKIT_INTERNAL_URL", "http://livekit:7880")


def _decode(token: str) -> dict:
    # The signature is what is checked; a test that mints a token at a fixed past time must not
    # fail because that time has since passed.
    return jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        options={"verify_aud": False, "verify_exp": False, "verify_nbf": False},
    )


def _admin(handler) -> LiveKitAdmin:
    return LiveKitAdmin(
        "http://livekit:7880/", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


# --- configuration ---------------------------------------------------------------------------


def test_it_is_not_configured_until_the_key_the_secret_and_the_public_url_are_set(
    monkeypatch,
) -> None:
    settings = get_settings()
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "LIVEKIT_PUBLIC_URL"):
        monkeypatch.setattr(settings, name, None)
    assert livekit.configured() is False
    assert livekit.get_livekit_admin() is None

    monkeypatch.setattr(settings, "LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "LIVEKIT_API_SECRET", SECRET)
    assert livekit.configured() is False  # still no public URL
    monkeypatch.setattr(settings, "LIVEKIT_PUBLIC_URL", "wss://example.test/livekit")
    assert livekit.configured() is True


def test_the_admin_client_needs_the_internal_url_too(configured, monkeypatch) -> None:
    assert isinstance(livekit.get_livekit_admin(), LiveKitAdmin)
    monkeypatch.setattr(get_settings(), "LIVEKIT_INTERNAL_URL", None)
    assert livekit.get_livekit_admin() is None


def test_a_room_name_is_derived_from_the_room_id() -> None:
    rid = uuid.uuid4()
    assert livekit.livekit_room_name(rid) == f"room-{rid}"


# --- the join token ---------------------------------------------------------------------------


def test_a_join_token_is_signed_scoped_and_expires(configured) -> None:
    issued = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    token, expires = livekit.join_token(
        identity="u-1", name="Anand", room="room-1", can_publish=False, now=issued
    )
    claims = _decode(token)
    assert claims["iss"] == API_KEY and claims["sub"] == "u-1" and claims["name"] == "Anand"
    assert claims["nbf"] == int(issued.timestamp())
    assert expires == issued + timedelta(hours=1)
    assert claims["exp"] == int(expires.timestamp())
    assert claims["video"] == {
        "room": "room-1",
        "roomJoin": True,
        "canSubscribe": True,
        "canPublish": False,
        "canPublishData": False,
    }


def test_a_token_that_can_publish_is_short_lived_and_a_listener_token_is_not(configured) -> None:
    # A hand-over cannot reach a token already issued (LiveKit re-applies a token's grants on every
    # join), so the token that can speak must expire quickly. Found by the review of #151.
    issued = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    lead = _decode(
        livekit.join_token(identity="u", name="n", room="r", can_publish=True, now=issued)[0]
    )
    listener = _decode(
        livekit.join_token(identity="u", name="n", room="r", can_publish=False, now=issued)[0]
    )
    assert lead["exp"] - lead["nbf"] <= 120
    assert listener["exp"] - listener["nbf"] == 3600


def test_only_can_publish_changes_between_a_listener_and_a_lead(configured) -> None:
    listener = _decode(livekit.join_token(identity="u", name="n", room="r", can_publish=False)[0])
    lead = _decode(livekit.join_token(identity="u", name="n", room="r", can_publish=True)[0])
    assert listener["video"]["canPublish"] is False and lead["video"]["canPublish"] is True
    assert {k: v for k, v in listener["video"].items() if k != "canPublish"} == {
        k: v for k, v in lead["video"].items() if k != "canPublish"
    }


def test_a_token_signed_with_another_secret_does_not_verify(configured) -> None:
    token, _ = livekit.join_token(identity="u", name="n", room="r", can_publish=True)
    with pytest.raises(Exception):  # noqa: B017 - any jose error: the signature is wrong
        jwt.decode(token, "some-other-secret-entirely-of-length-32", algorithms=["HS256"])


def test_the_secret_is_never_in_a_token(configured) -> None:
    token, _ = livekit.join_token(identity="u", name="n", room="r", can_publish=True)
    assert SECRET not in token


# --- the admin calls -----------------------------------------------------------------------------


def test_create_room_sends_the_limits_with_a_create_capable_admin_token(configured) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["claims"] = _decode(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(200, json={"name": "room-1"})

    _admin(handler).create_room("room-1")
    assert seen["url"] == "http://livekit:7880/twirp/livekit.RoomService/CreateRoom"
    assert seen["body"] == {"name": "room-1", "max_participants": 50, "empty_timeout": 600}
    assert seen["claims"]["iss"] == API_KEY
    assert seen["claims"]["video"] == {"roomAdmin": True, "room": "room-1", "roomCreate": True}
    assert seen["claims"]["exp"] - seen["claims"]["nbf"] <= 120  # a short-lived admin token


def test_set_can_publish_sends_the_permission_and_never_allows_data(configured) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["claims"] = _decode(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(200, json={})

    assert _admin(handler).set_can_publish("room-1", "u-1", True) is True
    assert seen["url"].endswith("/twirp/livekit.RoomService/UpdateParticipant")
    assert seen["body"] == {
        "room": "room-1",
        "identity": "u-1",
        "permission": {"can_subscribe": True, "can_publish": True, "can_publish_data": False},
    }
    assert seen["claims"]["video"] == {"roomAdmin": True, "room": "room-1"}

    _admin(handler).set_can_publish("room-1", "u-1", False)
    assert seen["body"]["permission"]["can_publish"] is False


def test_a_participant_who_is_not_connected_is_nothing_to_change(configured) -> None:
    admin = _admin(lambda request: httpx.Response(404, json={"code": "not_found"}))
    assert admin.set_can_publish("room-1", "u-1", True) is False


@pytest.mark.parametrize("status", [400, 401, 500, 503])
def test_any_other_failure_raises(configured, status) -> None:
    admin = _admin(lambda request: httpx.Response(status, json={"code": "x"}))
    with pytest.raises(LiveKitUnavailable) as caught:
        admin.set_can_publish("room-1", "u-1", True)
    assert str(status) in str(caught.value)
    assert SECRET not in str(caught.value)


def test_an_unreachable_livekit_raises_without_leaking_the_url_or_the_secret(configured) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(LiveKitUnavailable) as caught:
        _admin(handler).create_room("room-1")
    assert "ConnectError" in str(caught.value)
    assert SECRET not in str(caught.value) and "livekit:7880" not in str(caught.value)


def test_a_timeout_raises(configured) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LiveKitUnavailable):
        _admin(handler).set_can_publish("room-1", "u-1", False)


def test_create_room_failure_raises(configured) -> None:
    with pytest.raises(LiveKitUnavailable):
        _admin(lambda request: httpx.Response(500)).create_room("room-1")


def test_delete_room_needs_the_create_grant_as_well_as_admin(configured) -> None:
    # Found by infra/livekit/smoke.sh against a real LiveKit: with roomAdmin alone DeleteRoom is a 401.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["claims"] = _decode(request.headers["authorization"].removeprefix("Bearer "))
        return httpx.Response(200, json={})

    _admin(handler).delete_room("room-1")
    assert seen["claims"]["video"] == {"roomAdmin": True, "room": "room-1", "roomCreate": True}


def test_delete_room_treats_already_gone_as_done(configured) -> None:
    _admin(lambda request: httpx.Response(200, json={})).delete_room("room-1")
    _admin(lambda request: httpx.Response(404, json={})).delete_room("room-1")
    with pytest.raises(LiveKitUnavailable):
        _admin(lambda request: httpx.Response(500)).delete_room("room-1")


def test_a_proxy_in_the_environment_never_sees_the_call(configured, monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    admin = LiveKitAdmin("http://livekit:7880")
    assert admin._http._trust_env is False
