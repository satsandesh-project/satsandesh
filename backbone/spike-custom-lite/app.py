"""Spike B: custom-lite chat backbone (FastAPI WebSockets + Postgres outbox).

SPIKE CODE -- not production. No auth (user_id is a trusted query param),
no reconnection backoff, no connection pooling. The point is to answer
one question honestly: can this pattern deliver messages reliably and in
order, survive a crash, and handle two dispatchers without double-
delivery? See docs/adr/0002-chat-backbone.md for the findings.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional

import psycopg
from circles import OutboxCircleStore
from dispatcher import run_forever
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from registry import ConnectionRegistry

from db import DATABASE_URL, ensure_schema

registry = ConnectionRegistry()
circle_store = OutboxCircleStore()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_schema()
    task = asyncio.create_task(run_forever(lambda: registry))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="SatSandesh Spike B — custom-lite backbone", lifespan=lifespan)


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
    await circle_store.add_member(circle_id, req.user_id)
    return {"status": "ok"}


@app.delete("/circles/{circle_id}/members/{user_id}")
async def remove_member(circle_id: str, user_id: str):
    await circle_store.remove_member(circle_id, user_id)
    return {"status": "ok"}


@app.get("/circles/{circle_id}/members")
async def list_members(circle_id: str):
    return {"members": await circle_store.list_members(circle_id)}


@app.post("/circles/{circle_id}/announce")
async def announce(circle_id: str, req: AnnounceRequest):
    message_id = await circle_store.post_announcement(circle_id, req.sender_id, req.body)
    return {"message_id": message_id}


@app.get("/circles/{circle_id}/messages")
async def list_messages(circle_id: str, limit: int = 50, before: Optional[str] = None):
    messages = await circle_store.list_messages(circle_id, limit=limit, before=before)
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
    try:
        while True:
            # The spike only pushes server->client; block here until the
            # client disconnects (or sends something we simply ignore).
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        registry.remove(user_id, websocket)
