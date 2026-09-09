"""Spike B: custom-lite chat backbone (FastAPI WebSockets + Postgres outbox).

SPIKE CODE -- not production. No auth (user_id is a trusted query param),
no reconnection backoff, no connection pooling. The point is to answer
one question honestly: can this pattern deliver messages reliably and in
order, survive a crash, and handle two dispatchers without double-
delivery? See docs/adr/0002-chat-backbone.md for the findings.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

import psycopg
from circles import OutboxCircleStore
from dispatcher import run_forever
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from interfaces import BackboneUnavailable
from pydantic import BaseModel
from registry import ConnectionRegistry

from db import DATABASE_URL, ensure_schema

logger = logging.getLogger("spike.app")

registry = ConnectionRegistry()
circle_store = OutboxCircleStore()


def _dispatcher_exited(task: asyncio.Task) -> None:
    """The dispatcher task is the only thing delivering messages. If it
    ever stops, the app keeps answering /health with "ok" while silently
    delivering nothing -- which is exactly what used to happen when a
    single transient connect error escaped run_forever (create_task never
    retrieves an exception unless someone asks for it, so the failure was
    invisible). run_forever now swallows per-cycle errors itself, so this
    should be unreachable; it exists so that if it ever IS reached, it is
    loud rather than silent."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.critical("dispatcher task died; no messages will be delivered", exc_info=exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_schema()
    task = asyncio.create_task(run_forever(lambda: registry))
    task.add_done_callback(_dispatcher_exited)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="SatSandesh Spike B — custom-lite backbone", lifespan=lifespan)


# The store raises the contract's own error types (interfaces.py) rather
# than leaking psycopg exceptions; these turn them into honest status
# codes instead of a blanket 500. Without them, "the database is down" and
# "you asked for a circle that does not exist" were indistinguishable to a
# caller -- both arrived as an opaque 500.
@app.exception_handler(BackboneUnavailable)
async def _backbone_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def _not_found(request, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_request(request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


class SendRequest(BaseModel):
    conversation_id: str
    sender_id: str
    recipient_ids: List[str]
    body: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "spike-custom-lite"}


@app.post("/send")
async def send(req: SendRequest):
    """Writes the message and one outbox row per recipient in a single
    transaction. This is the whole point of the outbox pattern: if the
    process dies right after this commits, the delivery *obligation* is
    already durable on disk -- the dispatcher picks it up on its own,
    with no dependency on this request having survived."""
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO spike_messages (conversation_id, sender_id, body) "
                "VALUES (%s, %s, %s) RETURNING id",
                (req.conversation_id, req.sender_id, req.body),
            )
            row = await cur.fetchone()
            message_id = row[0]
            for recipient_id in req.recipient_ids:
                await cur.execute(
                    "INSERT INTO spike_outbox (message_id, recipient_id) VALUES (%s, %s)",
                    (message_id, recipient_id),
                )
        await conn.commit()
    return {"message_id": message_id}


# --- circles -------------------------------------------------------------
# Thin HTTP surface over OutboxCircleStore, so the gateway (a separate
# container) can reach it. The gateway talks to these routes and never to
# this module's internals -- that boundary is what ADR 0002's unresolved
# decision is being insulated behind. See backbone/interfaces.py.


def _circle_id(circle_id: str) -> str:
    """Circle ids in this backbone are Postgres BIGSERIALs. A non-numeric
    one used to reach circles.py's int() cast and surface as an unhandled
    ValueError -- a 500 on what is really a malformed request. Rejected
    here with a 422 instead, at the edge, so the store never sees input it
    cannot use."""
    if not circle_id.isdigit():
        raise HTTPException(status_code=422, detail="circle_id must be numeric")
    return circle_id


class CreateCircleRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    user_id: str


class AnnounceRequest(BaseModel):
    sender_id: str
    body: str


@app.post("/circles")
async def create_circle(req: CreateCircleRequest):
    circle_id = await circle_store.create_circle(req.name)
    return {"circle_id": circle_id}


@app.post("/circles/{circle_id}/members")
async def add_member(circle_id: str, req: AddMemberRequest):
    await circle_store.add_member(_circle_id(circle_id), req.user_id)
    return {"status": "ok"}


@app.delete("/circles/{circle_id}/members/{user_id}")
async def remove_member(circle_id: str, user_id: str):
    await circle_store.remove_member(_circle_id(circle_id), user_id)
    return {"status": "ok"}


@app.get("/circles/{circle_id}/members")
async def list_members(circle_id: str):
    return {"members": await circle_store.list_members(_circle_id(circle_id))}


@app.post("/circles/{circle_id}/announce")
async def announce(circle_id: str, req: AnnounceRequest):
    message_id = await circle_store.post_announcement(
        _circle_id(circle_id), req.sender_id, req.body
    )
    return {"message_id": message_id}


@app.get("/circles/{circle_id}/messages")
async def list_messages(circle_id: str, limit: int = 50, before: Optional[str] = None):
    if before is not None and not before.isdigit():
        raise HTTPException(status_code=422, detail="before must be a numeric message id")
    messages = await circle_store.list_messages(_circle_id(circle_id), limit=limit, before=before)
    return {
        "messages": [
            {
                "id": m.id,
                "circle_id": m.circle_id,
                "sender_id": m.sender_id,
                "body": m.body,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, user_id: str):
    """No auth this week -- user_id is just a query param the caller
    asserts. Real identity is a later task, not a Spike B concern."""
    await websocket.accept()
    registry.add(user_id, websocket)
    # No backoff reset needed here: the dispatcher's claim query treats
    # anyone in the registry as always-due (see its CLAIM_SQL), so simply
    # being connected is what makes this user's backlog claimable again.
    try:
        while True:
            # The spike only pushes server->client; block here until the
            # client disconnects (or sends something we simply ignore).
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        registry.remove(user_id, websocket)
