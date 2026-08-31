"""Low-level Matrix Client-Server + Application-Service HTTP calls.

Every call in this module was run against a real Tuwunel instance before
being written here -- not assumed from the spec or from Spike A's Conduit
findings. Where behaviour genuinely differs from what Spike A found on
Conduit, that's noted inline (and in docs/adr/0002-chat-backbone.md).

No FastAPI, no CircleBackbone here -- this is pure Matrix HTTP plumbing.
matrix_circle_store.py is what maps it onto the interface.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("matrix_client")


class MatrixClient:
    def __init__(
        self, homeserver_url: str, as_token: str, server_name: str, http_timeout: float = 10.0
    ):
        self._base = homeserver_url.rstrip("/")
        self._as_token = as_token
        self.server_name = server_name
        self._timeout = http_timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._as_token}"}

    async def _request(
        self, method: str, path: str, *, impersonate: Optional[str] = None, **kwargs
    ) -> httpx.Response:
        params = kwargs.pop("params", {}) or {}
        if impersonate:
            # AS impersonation: the `user_id` query param on ANY C-S API
            # call makes the homeserver treat the request as coming from
            # that user, as long as it's within our exclusive namespace.
            # Verified directly (room creation, invite, join, send, all
            # impersonated this way) rather than assumed from the spec.
            params["user_id"] = impersonate
        url = f"{self._base}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.request(
                method, url, headers=self._headers(), params=params, **kwargs
            )
        return resp

    # --- user provisioning ------------------------------------------------

    async def ensure_as_user(self, localpart: str) -> str:
        """Registers a user in our AS namespace if not already present.
        Returns the full Matrix user id.

        Verified idempotent-in-practice: a second registration attempt
        for an existing user returns 400 M_USER_IN_USE, treated as
        success here -- matches CircleBackbone's add_member being
        idempotent by contract.

        Also verified (not assumed): on Tuwunel, the AS's own
        sender_localpart user was ALREADY registered at the moment the
        appservice registration itself was accepted -- Tuwunel
        auto-provisions it. Spike A found the opposite on Conduit
        (required an explicit registration call). Calling this
        unconditionally for every localpart, bot included, is correct on
        both: a no-op M_USER_IN_USE on Tuwunel, a real registration on
        Conduit-like servers that don't auto-provision.
        """
        resp = await self._request(
            "POST",
            "/_matrix/client/v3/register",
            json={"type": "m.login.application_service", "username": localpart},
        )
        if resp.status_code == 200:
            return resp.json()["user_id"]
        content_type = resp.headers.get("content-type", "")
        body = resp.json() if content_type.startswith("application/json") else {}
        if resp.status_code == 400 and body.get("errcode") == "M_USER_IN_USE":
            # The homeserver never gives us the user id in this response
            # (it's a rejection, not a success), so reconstruct it -- Matrix
            # user ids are deterministic from localpart + server_name.
            return f"@{localpart}:{self.server_name}"
        raise MatrixError(f"failed to register {localpart!r}: {resp.status_code} {resp.text}")

    # --- rooms ---------------------------------------------------------

    async def create_room(self, name: str, creator_user_id: str) -> str:
        """Creates a room impersonating `creator_user_id`. The creator is
        automatically joined by Matrix's own room-creation semantics --
        verified directly (joined_members immediately after creation
        showed only the creator, no separate join call needed). This is
        exactly why Spike A's Conduit join bug doesn't apply to circle
        creation: the bug was in the join-on-invite path, which room
        creation never touches.

        No `initial_state` is passed, which means no m.room.encryption
        event -- confirmed empirically (GET .../state/m.room.encryption
        returned 404 on a room created this way), not assumed from
        "encryption is opt-in" being generally true.
        """
        resp = await self._request(
            "POST",
            "/_matrix/client/v3/createRoom",
            impersonate=creator_user_id,
            json={"name": name, "preset": "private_chat"},
        )
        if resp.status_code != 200:
            raise MatrixError(f"create_room failed: {resp.status_code} {resp.text}")
        return resp.json()["room_id"]

    async def invite(self, room_id: str, inviter: str, invitee: str) -> None:
        resp = await self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{room_id}/invite",
            impersonate=inviter,
            json={"user_id": invitee},
        )
        # Already-invited/already-joined is not an error for our purposes
        # -- add_member is idempotent per the interface contract.
        if resp.status_code not in (200,) and "already" not in resp.text.lower():
            raise MatrixError(f"invite failed: {resp.status_code} {resp.text}")

    async def join(self, room_id: str, user_id: str) -> None:
        """Joins impersonating `user_id` directly -- no separate invite
        acceptance step from the user, since AS impersonation can act on
        behalf of any user in its own namespace. Verified: this succeeds
        immediately after an invite, with no "No server available to
        assist in joining" error -- the exact failure Spike A found on
        Conduit for the BOT's own join. Confirms that failure doesn't
        reproduce here for member joins on Tuwunel either.
        """
        resp = await self._request(
            "POST", f"/_matrix/client/v3/rooms/{room_id}/join", impersonate=user_id
        )
        if resp.status_code != 200 and "already" not in resp.text.lower():
            raise MatrixError(f"join failed for {user_id}: {resp.status_code} {resp.text}")

    async def kick(self, room_id: str, kicker: str, target: str, reason: str = "") -> None:
        resp = await self._request(
            "POST",
            f"/_matrix/client/v3/rooms/{room_id}/kick",
            impersonate=kicker,
            json={"user_id": target, "reason": reason},
        )
        # Kicking someone not currently joined (already removed, or never
        # joined) is not an error -- remove_member is idempotent per the
        # interface contract.
        if resp.status_code not in (200,) and "not in the room" not in resp.text.lower():
            raise MatrixError(f"kick failed for {target}: {resp.status_code} {resp.text}")

    async def joined_members(self, room_id: str, as_user: str) -> List[str]:
        resp = await self._request(
            "GET", f"/_matrix/client/v3/rooms/{room_id}/joined_members", impersonate=as_user
        )
        if resp.status_code != 200:
            raise MatrixError(f"joined_members failed: {resp.status_code} {resp.text}")
        return list(resp.json().get("joined", {}).keys())

    # --- messages --------------------------------------------------------

    async def send_message(self, room_id: str, sender: str, content: Dict[str, Any]) -> str:
        txn_id = uuid.uuid4().hex
        resp = await self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn_id}",
            impersonate=sender,
            json=content,
        )
        if resp.status_code != 200:
            raise MatrixError(f"send_message failed: {resp.status_code} {resp.text}")
        return resp.json()["event_id"]

    async def messages(
        self,
        room_id: str,
        as_user: str,
        limit: int = 50,
        before_event_id: Optional[str] = None,
    ) -> List[dict]:
        """Backwards-paginated room timeline, newest first.

        `before_event_id`: Matrix's /messages endpoint takes an opaque
        pagination TOKEN via `from`, not an event id -- verified directly
        that passing an event id there doesn't work as one might assume.
        The documented way to convert an event id into a token is
        /context/{eventId}, whose `start` token anchors a subsequent
        /messages?dir=b call to return events strictly before that
        point -- verified with a real 3-message sequence and an anchor in
        the middle, confirming the later messages were correctly excluded.
        """
        params = {"dir": "b", "limit": limit}
        if before_event_id is not None:
            ctx = await self._request(
                "GET",
                f"/_matrix/client/v3/rooms/{room_id}/context/{before_event_id}",
                impersonate=as_user,
                params={"limit": 0},
            )
            if ctx.status_code != 200:
                raise MatrixError(f"context lookup failed: {ctx.status_code} {ctx.text}")
            params["from"] = ctx.json()["start"]

        resp = await self._request(
            "GET",
            f"/_matrix/client/v3/rooms/{room_id}/messages",
            impersonate=as_user,
            params=params,
        )
        if resp.status_code != 200:
            raise MatrixError(f"messages failed: {resp.status_code} {resp.text}")
        return resp.json().get("chunk", [])


class MatrixError(RuntimeError):
    """Raised for any Matrix API call that didn't succeed. Caught and
    translated to interfaces.BackboneUnavailable at the store layer, not
    here -- this module doesn't know about CircleBackbone."""
