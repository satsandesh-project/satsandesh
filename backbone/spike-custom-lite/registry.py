"""In-memory map of user_id -> live WebSocket connections.

This is the load-bearing simplification of the whole spike: it only knows
about sockets held by *this* process. Run two instances of this app and a
recipient connected to instance A is invisible to instance B's dispatcher
loop -- that row just sits pending until A's dispatcher claims it. Fine
for a single-instance spike; a real deployment would need this to be
shared state (e.g. Redis) once there's more than one app process, which
is exactly the kind of cost this spike exists to surface, not hide.
"""

from typing import Dict, Set

from fastapi import WebSocket


class ConnectionRegistry:
    def __init__(self) -> None:
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

    def get(self, user_id: str) -> Set[WebSocket]:
        return self._connections.get(user_id, set())

    def is_online(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))
