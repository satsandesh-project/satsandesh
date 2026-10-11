"""LiveKit, the audio side of bhajan rooms (app/rooms.py): the access tokens the gateway issues,
and the server calls it makes to change who may speak.

Two things are enforced HERE, on the server, never by the app:

1. **A join token carries `canPublish` only for the current lead.** Everyone else joins with
   `canPublish: false`, so even a modified app cannot put its microphone on the air.
2. **When the lead changes, the gateway changes the connected person's permission** through
   LiveKit's admin API (`UpdateParticipant`), so a hand-over does not need anyone to rejoin: the
   old lead is silenced and the new one is allowed, by the server.

Access tokens are plain HS256 JWTs (LiveKit's format), minted with python-jose like the gateway's
own session tokens, so no LiveKit SDK is needed. The admin calls are LiveKit's Twirp JSON API over
HTTP with an admin token. Nothing here logs a token or the secret.

Not configured (`LIVEKIT_*` unset): `configured()` is False, the token route answers 503, and
hand-overs change the queue only. That keeps every non-audio path, and every test, working with
no LiveKit running.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from jose import jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
# The room is built for about this many people; LiveKit refuses the 51st.
MAX_PARTICIPANTS = 50
# A room nobody is in is closed by LiveKit after this long (seconds); the gateway's own
# `rooms.status` is what says whether the room is open.
EMPTY_TIMEOUT_S = 600
_TWIRP = "/twirp/livekit.RoomService/"


class LiveKitUnavailable(Exception):
    """LiveKit is not configured, or a call to it failed. The route turns this into a 502/503."""


def configured() -> bool:
    s = get_settings()
    return bool(s.LIVEKIT_API_KEY and s.LIVEKIT_API_SECRET and s.LIVEKIT_PUBLIC_URL)


def livekit_room_name(room_id: uuid.UUID | str) -> str:
    return f"room-{room_id}"


def _encode(claims: dict) -> str:
    return jwt.encode(claims, get_settings().LIVEKIT_API_SECRET, algorithm=ALGORITHM)


def join_token(
    *,
    identity: str,
    name: str,
    room: str,
    can_publish: bool,
    ttl_seconds: int = 3600,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """A token that lets `identity` join `room` -- and speak only if `can_publish`.

    `canPublishData` is always false: nobody in a room sends data messages, and a listener
    must not be able to. The token is scoped to one room."""
    settings = get_settings()
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(seconds=ttl_seconds)
    token = _encode(
        {
            "iss": settings.LIVEKIT_API_KEY,
            "sub": identity,
            "name": name,
            "nbf": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
            "video": {
                "room": room,
                "roomJoin": True,
                "canSubscribe": True,
                "canPublish": can_publish,
                "canPublishData": False,
            },
        }
    )
    return token, expires


def _admin_token(room: str | None = None, *, create: bool = False) -> str:
    now = datetime.now(UTC)
    video: dict = {"roomAdmin": True}
    if room:
        video["room"] = room
    if create:
        video["roomCreate"] = True
    return _encode(
        {
            "iss": get_settings().LIVEKIT_API_KEY,
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=1)).timestamp()),
            "video": video,
        }
    )


class LiveKitAdmin:
    """The few admin calls the gateway makes. Each is one short HTTP request."""

    def __init__(self, base_url: str, *, http: httpx.Client | None = None, timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        # trust_env=False: this is a call to our own service; a system proxy must not see it.
        self._http = http if http is not None else httpx.Client(trust_env=False)
        self._timeout = timeout

    def _call(self, method: str, payload: dict, *, room: str | None, create: bool = False):
        try:
            response = self._http.post(
                f"{self._base}{_TWIRP}{method}",
                json=payload,
                headers={"Authorization": f"Bearer {_admin_token(room, create=create)}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise LiveKitUnavailable(f"LiveKit {method} failed: {type(exc).__name__}") from exc
        return response

    def create_room(self, name: str) -> None:
        """Make the room exist with the right limits. Idempotent: LiveKit returns the existing
        room if there is one."""
        response = self._call(
            "CreateRoom",
            {
                "name": name,
                "max_participants": MAX_PARTICIPANTS,
                "empty_timeout": EMPTY_TIMEOUT_S,
            },
            room=name,
            create=True,
        )
        if response.status_code != 200:
            raise LiveKitUnavailable(f"LiveKit CreateRoom answered {response.status_code}")

    def set_can_publish(self, room: str, identity: str, can_publish: bool) -> bool:
        """Allow or forbid `identity` to speak in `room` right now. Returns False if that person
        is not connected (a 404 from LiveKit): nothing to change, and when they do join the token
        they are issued carries the right permission. Any other failure raises."""
        response = self._call(
            "UpdateParticipant",
            {
                "room": room,
                "identity": identity,
                "permission": {
                    "can_subscribe": True,
                    "can_publish": can_publish,
                    "can_publish_data": False,
                },
            },
            room=room,
        )
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise LiveKitUnavailable(f"LiveKit UpdateParticipant answered {response.status_code}")
        return True

    def delete_room(self, name: str) -> None:
        """Disconnect everyone and remove the room (a 404: it was already gone)."""
        response = self._call("DeleteRoom", {"room": name}, room=name)
        if response.status_code not in (200, 404):
            raise LiveKitUnavailable(f"LiveKit DeleteRoom answered {response.status_code}")


def get_livekit_admin() -> LiveKitAdmin | None:
    """The admin client, or None when LiveKit is not configured. Tests replace this."""
    settings = get_settings()
    if not configured() or not settings.LIVEKIT_INTERNAL_URL:
        return None
    return LiveKitAdmin(settings.LIVEKIT_INTERNAL_URL)
