"""Gateway-level WebSocket endpoint for live chat.

Design note, since this is a real architectural choice and not an
accident: `backbone/interfaces.py`'s CircleBackbone has no push/subscribe
method -- it's create/add/remove/list/post/list_messages, all pull-based.
Adding a push method to the interface was considered and rejected for
this week's scope: it would mean every backbone implementation
(spike-custom-lite's outbox AND the live Matrix one) needs a matching
subscribe mechanism, a bigger change than "wire the client to the
gateway" calls for.

Instead, delivery here is two separate, honestly-separate things:
  1. Durable history: every message is written through the backbone
     interface (post_announcement), same as any other circle post --
     durable regardless of which backbone is live, retrievable later via
     GET /circles/{id}/messages.
  2. Live push: entirely a gateway-local concern. A message is broadcast
     to whichever sockets are connected to THIS gateway process right
     now, via an in-memory registry. A client that reconnects (or opens a
     new tab) gets a `history` frame from list_messages to catch up, not
     a replay of the live broadcast.

This means live push does NOT survive a gateway restart or scale past one
gateway instance -- same "single-process registry" limitation
spike-custom-lite's dispatcher named honestly in Week 2/3, now true here
too, for the same underlying reason (no shared registry backing it).
Named here rather than discovered the hard way in a multi-instance
deployment.
"""

import logging
from typing import Dict, Set

from auth import AuthError, verify_token
from fastapi import WebSocket, WebSocketDisconnect
from interfaces import BackboneUnavailable

logger = logging.getLogger("gateway.ws")

DEFAULT_CIRCLE_NAME = "General"


class ConnectionRegistry:
    """Same shape as spike-custom-lite's registry.py -- in-memory
    user_id -> live sockets, gateway-local only."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    def add(self, user_id: str, ws: WebSocket) -> None:
        self._connections.setdefault(user_id, set()).add(ws)

    def remove(self, user_id: str, ws: WebSocket) -> None:
        sockets = self._connections.get(user_id)
        if not sockets:
            return
        sockets.discard(ws)
        if not sockets:
            del self._connections[user_id]

    def remove_everywhere(self, ws: WebSocket) -> None:
        """For cleaning up a socket that failed mid-broadcast, when the
        caller only has the socket, not which user_id it belonged to."""
        for user_id in list(self._connections.keys()):
            self._connections[user_id].discard(ws)
            if not self._connections[user_id]:
                del self._connections[user_id]

    def all_sockets(self):
        for sockets in self._connections.values():
            yield from sockets

    def online_count(self) -> int:
        return sum(len(s) for s in self._connections.values())


registry = ConnectionRegistry()

# Memoized once a circle exists. Deliberately re-attempted lazily (see
# _ensure_default_circle) rather than only at startup: if the gateway
# comes up before a backbone profile does, the WS relay should still work
# for live-only delivery, and should self-heal into persisting once a
# backbone becomes reachable, rather than being stuck unpersisted forever
# because of a one-time startup-order race.
_default_circle_id = None


async def _ensure_default_circle(backbone) -> str:
    global _default_circle_id
    if _default_circle_id is not None:
        return _default_circle_id
    circle_id = await backbone.create_circle(DEFAULT_CIRCLE_NAME)
    _default_circle_id = circle_id
    logger.info("default circle ready: %s", circle_id)
    return circle_id


async def _send_json_safely(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        logger.warning("send failed to a socket", exc_info=True)
        return False


async def websocket_endpoint(websocket: WebSocket, token: str, get_backbone):
    try:
        user_id = verify_token(token)
    except AuthError as exc:
        # accept() THEN close(4401) -- not close() before accept(), despite
        # that looking like the more obviously-correct order for rejecting
        # an unauthenticated socket. Verified wrong the hard way once
        # already this project (services/gateway/docs/DECISIONS.md, found
        # by a teammate working the team repo's own gateway in parallel):
        # uvicorn can only send a real WS close frame once the handshake
        # completes, so closing before accept() collapses to a bare HTTP
        # 403 and a real browser reports it as the ambiguous code 1006
        # (indistinguishable from a dead network) instead of 4401 -- which
        # is exactly the code clients/elder-app's own reconnect JS checks
        # for to know "stop retrying, this was a real auth rejection, not
        # a dropped connection" (see gateway_ws_proof.py's `event.code ===
        # 4401` check). Closing before accept() would have made that check
        # silently never fire. The socket is still never added to
        # `registry` and never reaches the receive loop below, so this
        # doesn't weaken the "unauthenticated socket sees no application
        # traffic" guarantee -- only the close-frame delivery mechanics
        # change.
        await websocket.accept()
        await websocket.close(code=4401, reason=f"invalid token: {exc}")
        return

    await websocket.accept()
    registry.add(user_id, websocket)
    logger.info("connected: %s (online: %d)", user_id, registry.online_count())

    backbone = get_backbone()
    circle_id = None
    persistence_note = None
    try:
        circle_id = await _ensure_default_circle(backbone)
        await backbone.add_member(circle_id, user_id)
        history = await backbone.list_messages(circle_id, limit=30)
        await websocket.send_json(
            {
                "type": "history",
                "circle_id": circle_id,
                "messages": [
                    {
                        "sender_id": m.sender_id,
                        "body": m.body,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in reversed(history)  # oldest first for a message list UI
                ],
            }
        )
    except BackboneUnavailable as exc:
        # Live relay still works without a backbone up -- see module
        # docstring. Told to the client explicitly rather than silently
        # degraded, so a placeholder UI can show "not saved" honestly.
        persistence_note = str(exc)
        logger.warning("backbone unavailable at connect for %s: %s", user_id, exc)
        await websocket.send_json(
            {
                "type": "history",
                "circle_id": None,
                "messages": [],
                "warning": f"messages will not be saved: {persistence_note}",
            }
        )

    try:
        while True:
            data = await websocket.receive_json()
            body = (data.get("body") or "").strip()
            if not body:
                continue
            body = body[:2000]  # a stray runaway paste shouldn't wedge every peer's UI

            if circle_id is not None:
                try:
                    await backbone.post_announcement(circle_id, user_id, body)
                except BackboneUnavailable as exc:
                    logger.warning("post failed for %s: %s", user_id, exc)
                    await websocket.send_json(
                        {"type": "error", "detail": f"message not saved: {exc}"}
                    )
            else:
                # Backbone wasn't up at connect time -- try once more now,
                # in case it's come up since (self-heal, see docstring).
                try:
                    circle_id = await _ensure_default_circle(backbone)
                    await backbone.add_member(circle_id, user_id)
                    await backbone.post_announcement(circle_id, user_id, body)
                except BackboneUnavailable as exc:
                    logger.warning("post failed (no backbone) for %s: %s", user_id, exc)
                    await websocket.send_json(
                        {"type": "error", "detail": f"message not saved: {exc}"}
                    )

            payload = {"type": "message", "sender_id": user_id, "body": body}
            dead = []
            for peer in list(registry.all_sockets()):
                if not await _send_json_safely(peer, payload):
                    dead.append(peer)
            # Sockets that failed mid-broadcast are cleaned up lazily here
            # rather than via a health-check loop -- the disconnect
            # handler below is the primary cleanup path; this just avoids
            # repeatedly retrying an obviously-dead socket within the same
            # broadcast fan-out.
            for peer in dead:
                registry.remove_everywhere(peer)

    except WebSocketDisconnect:
        pass
    finally:
        registry.remove(user_id, websocket)
        logger.info("disconnected: %s (online: %d)", user_id, registry.online_count())
