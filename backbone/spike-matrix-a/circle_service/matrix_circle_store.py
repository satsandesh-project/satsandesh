"""MatrixCircleStore: CircleBackbone implemented on Tuwunel.

DECISION -- sender attribution in post_announcement (required by this
week's task to be picked and written down, not left implicit):

**The bot posts every announcement as itself, recording the real
sender_id in a custom content field, rather than impersonating
sender_id to post directly.**

Why: the interface's own contract says "Sender need not be a member (an
announcement channel is one-to-many by design)." Matrix requires
membership=join in a room to send `m.room.message` into it. If sender_id
isn't necessarily a member, impersonating them to post would either (a)
require silently joining them first -- which would make them incorrectly
appear in list_members, a real membership state the interface says
should only reflect actual added members, or (b) simply fail whenever
the sender isn't a member. Bot-posts-with-recorded-sender has neither
problem and works unconditionally. The custom field is
"org.satsandesh.sender_id", namespaced per Matrix convention for
non-spec content keys.

DECISION -- offline delivery, found rather than assumed (this week's
other required finding):

The outbox's guarantee (Week 2/3) was that a member's obligation is
resolved and recorded *at post time*: removal stops future
announcements, but a member added after a post never receives that
earlier message, and an offline member's redelivery is retried by an
active dispatcher.

Matrix's model is different in kind, verified directly against a real
Tuwunel instance rather than assumed from Spike A's Conduit findings
(which didn't test this): a new room's default history_visibility is
"shared", meaning any CURRENTLY joined member can read the room's full
history regardless of when they joined -- including messages sent
*before* they joined. Tested concretely: posted a message, then added a
brand-new member who was never invited or present beforehand, and that
member's own read access (impersonated, not the bot's) returned the
pre-join message. There is also no separate "offline" state to test in
the outbox sense -- Matrix's /messages is pull-based and always returns
full history to any current member; there's no push/retry queue to
observe succeeding or failing. This is a real, qualitative difference
from the outbox's per-recipient obligation model, not a smaller version
of the same guarantee -- see docs/adr/0002-chat-backbone.md.
"""

from datetime import datetime, timezone
from typing import List, Optional

from interfaces import BackboneUnavailable, CircleBackbone, CircleMessage
from matrix_client import MatrixClient, MatrixError

SENDER_ID_KEY = "org.satsandesh.sender_id"


def _localpart(user_id: str) -> str:
    """Maps a plain caller-asserted user_id (e.g. "bob") to a Matrix
    localpart. Deterministic and (for the lowercase/underscore ids this
    system actually uses, per Week 3's own test fixtures) reversible by
    stripping the prefix -- see _from_mxid. Mixed-case ids would lose
    case on the round trip; every id in this system's tests is already
    lowercase, so this is a documented spike-level simplification, not a
    silent one."""
    return f"circle_{user_id.lower()}"


def _to_mxid(user_id: str, server_name: str) -> str:
    return f"@{_localpart(user_id)}:{server_name}"


def _from_mxid(mxid: str, server_name: str) -> str:
    prefix = "@circle_"
    suffix = f":{server_name}"
    if not (mxid.startswith(prefix) and mxid.endswith(suffix)):
        raise ValueError(f"not a circle-namespace user id: {mxid!r}")
    return mxid[len(prefix) : -len(suffix)]


class MatrixCircleStore(CircleBackbone):
    def __init__(
        self,
        homeserver_url: str,
        as_token: str,
        server_name: str,
        bot_localpart: str,
    ):
        self._client = MatrixClient(homeserver_url, as_token, server_name)
        self._server_name = server_name
        self._bot_id = f"@{bot_localpart}:{server_name}"
        self._bot_localpart = bot_localpart

    async def _ensure_bot(self) -> None:
        await self._client.ensure_as_user(self._bot_localpart)

    def _catch(self, action: str):
        return _CatchMatrixError(action)

    async def create_circle(self, name: str) -> str:
        async with self._catch("create_circle"):
            await self._ensure_bot()
            room_id = await self._client.create_room(name, creator_user_id=self._bot_id)
            return room_id

    async def add_member(self, circle_id: str, user_id: str) -> None:
        async with self._catch("add_member"):
            mxid = _to_mxid(user_id, self._server_name)
            await self._client.ensure_as_user(_localpart(user_id))
            await self._client.invite(circle_id, inviter=self._bot_id, invitee=mxid)
            await self._client.join(circle_id, mxid)

    async def remove_member(self, circle_id: str, user_id: str) -> None:
        async with self._catch("remove_member"):
            mxid = _to_mxid(user_id, self._server_name)
            await self._client.kick(
                circle_id, kicker=self._bot_id, target=mxid, reason="removed from circle"
            )

    async def list_members(self, circle_id: str) -> List[str]:
        async with self._catch("list_members"):
            mxids = await self._client.joined_members(circle_id, as_user=self._bot_id)
            # The bot itself is always a joined member (room creator) --
            # it's infrastructure, not a circle member, so it's excluded.
            # Verified this filtering is necessary: joined_members right
            # after room creation returned exactly {bot}, nothing else.
            members = [
                _from_mxid(m, self._server_name)
                for m in mxids
                if m != self._bot_id and m.startswith("@circle_")
            ]
            return sorted(members)

    async def post_announcement(self, circle_id: str, sender_id: str, body: str) -> str:
        # See module docstring: bot posts as itself, sender recorded in
        # content, not impersonated -- because sender need not be a member.
        async with self._catch("post_announcement"):
            await self._ensure_bot()
            event_id = await self._client.send_message(
                circle_id,
                sender=self._bot_id,
                content={
                    "msgtype": "m.text",
                    "body": body,
                    SENDER_ID_KEY: sender_id,
                },
            )
            return event_id

    async def list_messages(
        self, circle_id: str, limit: int = 50, before: Optional[str] = None
    ) -> List[CircleMessage]:
        async with self._catch("list_messages"):
            events = await self._client.messages(
                circle_id, as_user=self._bot_id, limit=limit, before_event_id=before
            )
            out = []
            for event in events:
                if event.get("type") != "m.room.message":
                    continue
                content = event.get("content", {})
                sender_id = content.get(SENDER_ID_KEY)
                if sender_id is None:
                    # A message that didn't come through post_announcement
                    # (e.g. sent directly by some other client during
                    # manual poking). Fall back to the raw Matrix sender
                    # rather than dropping the message.
                    sender_id = event.get("sender", "")
                out.append(
                    CircleMessage(
                        id=event["event_id"],
                        circle_id=circle_id,
                        sender_id=sender_id,
                        body=content.get("body", ""),
                        created_at=datetime.fromtimestamp(
                            event.get("origin_server_ts", 0) / 1000, tz=timezone.utc
                        ),
                    )
                )
            return out


class _CatchMatrixError:
    """Translates MatrixError into the contract's BackboneUnavailable at
    the store boundary, so gateway/circles.py only ever has to know about
    one exception type regardless of which backbone is behind it."""

    def __init__(self, action: str):
        self._action = action

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None and issubclass(exc_type, MatrixError):
            raise BackboneUnavailable(f"{self._action}: {exc}") from exc
        return False
