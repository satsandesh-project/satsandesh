from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.auth import user_from_token

router = APIRouter()


class ConnectionManager:
    # In-memory only — no Redis, no database. Fine for a single gateway
    # process; won't survive a restart or scale past one instance, which is
    # exactly what real message delivery will need to fix later.
    #
    # send_to_user/broadcast were deliberately removed (Week 1 review): no
    # caller, no coverage, and both iterated _connections with no lock around
    # it — a concurrency bug nobody had reasoned about. Week 3's real fan-out
    # will come with real requirements (ordering, backpressure, locking) and
    # a real caller to test against; this is an intentional omission, not
    # something forgotten.
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
        # Deliberately accept() before close(): uvicorn can only send a real
        # WS close frame after the handshake completes, so closing before
        # accept() collapses to a bare HTTP 403 and browsers report ambiguous
        # code 1006 instead of 1008 — see docs/DECISIONS.md for the full
        # mechanism and why the distinction matters for Week 3's reconnect
        # logic.
        await websocket.accept()
        await websocket.close(code=1008, reason="missing_or_invalid_token")
        return

    await manager.connect(user.id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # ECHO STUB: this line is what becomes real message delivery
            # (persist, then fan out to recipients) once the chat backbone
            # exists. Not built yet — the fan-out methods this will need
            # were deliberately not pre-built either, see ConnectionManager
            # above.
            await websocket.send_text(data)
    except WebSocketDisconnect:
        # Normal control flow, not an error: phones sleep, lifts lose signal,
        # Wi-Fi hands over to 4G. Every one of those looks identical to a
        # deliberate disconnect from the server's side, so this is not logged
        # or treated as a failure — it's the expected way a session ends.
        pass
    finally:
        manager.disconnect(user.id, websocket)
