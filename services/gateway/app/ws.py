from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.auth import user_from_token

router = APIRouter()


class ConnectionManager:
    # In-memory only — no Redis, no database. Fine for a single gateway
    # process; won't survive a restart or scale past one instance, which is
    # exactly what real message delivery will need to fix later.
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(user_id)
        if connections is None:
            return
        connections.discard(websocket)
        if not connections:
            del self._connections[user_id]

    async def send_to_user(self, user_id: str, message: str) -> None:
        for websocket in self._connections.get(user_id, set()):
            await websocket.send_text(message)

    async def broadcast(self, message: str) -> None:
        for connections in self._connections.values():
            for websocket in connections:
                await websocket.send_text(message)


manager = ConnectionManager()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    # Browsers cannot set custom headers (e.g. Authorization) on a WebSocket
    # handshake, so Depends(HTTPBearer) — which reads that header — can't
    # authenticate this route the way it does /me. The token travels as a
    # query param instead; user_from_token is the same stub (soon: real JWT
    # verification) that get_current_user uses, just fed a differently-sourced
    # token string, so there's one auth implementation, not two.
    token = websocket.query_params.get("token")
    try:
        user = user_from_token(token)
    except HTTPException:
        # We accept() before closing, even though the peer isn't authenticated
        # yet. That looks backwards, but it's forced by how uvicorn handles a
        # close sent before accept: it never completes the WS opening
        # handshake, so it can't send a real close frame, and it collapses
        # the rejection to a flat HTTP 403 — discarding whatever close code
        # we asked for entirely. A real browser then reports that as close
        # code 1006 (abnormal closure), identical to what it reports for a
        # dead network. That's unacceptable for reconnect logic: a client
        # with a bad token needs to be able to tell "stop retrying, go
        # re-authenticate" (1008) apart from "network blip, keep retrying
        # with backoff" (1006) — see docs/DECISIONS.md.
        #
        # accept()-then-close() sends a real WS close frame with our code and
        # reason intact. We close immediately, before manager.connect() and
        # before the receive loop, so no connection is ever registered and no
        # data is ever read from this socket — the "acceptance" is nominal,
        # not an open door.
        await websocket.accept()
        await websocket.close(code=1008, reason="missing_or_invalid_token")
        return

    await manager.connect(user.id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # ECHO STUB: this line is what becomes real message delivery
            # (persist, then manager.send_to_user/broadcast to recipients)
            # once the chat backbone exists. Not built yet — see README/PR
            # notes for why persistence must happen before the push.
            await websocket.send_text(data)
    except WebSocketDisconnect:
        # Normal control flow, not an error: phones sleep, lifts lose signal,
        # Wi-Fi hands over to 4G. Every one of those looks identical to a
        # deliberate disconnect from the server's side, so this is not logged
        # or treated as a failure — it's the expected way a session ends.
        pass
    finally:
        manager.disconnect(user.id, websocket)
