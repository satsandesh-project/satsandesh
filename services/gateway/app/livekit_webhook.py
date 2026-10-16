"""LiveKit's webhook, for bhajan rooms: the check that runs when somebody joins.

Why it exists. A join token's `canPublish` is applied again every time someone joins with it, and
the gateway's hand-over (`app/rooms.py` `_sync_audio`) changes only the session LiveKit has at that
moment. So a person who led, with a token still inside its two minutes (`app/livekit.py`
`PUBLISH_TOKEN_TTL_S`), could rejoin as a speaker; and a person made the lead just before they
connected joins muted. This closes both: when LiveKit says `participant_joined`, the gateway looks
at the database and sets that person's permission to what it should be (can publish only if they
are the room's lead right now). Found by M2's review of #151; OPEN_QUESTIONS #35.

The route is for LiveKit only, inside the compose network (`docker-compose.livekit.yml` points
LiveKit's `webhook.urls` at it). Caddy does not route it, so it is not reachable from outside, and
a request must still carry LiveKit's signature: an `Authorization` token signed with the API
secret whose `sha256` claim is the hash of the body. Anything else is a 401.

It takes the room row lock first, like a hand-over, so a join that arrives mid-hand-over waits and
then sees the new lead. If LiveKit cannot be told the answer is a 502; the person keeps whatever
their token gave them, which is why the short token remains the first line of defence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import livekit
from app.config import get_settings
from app.db.base import get_db
from app.db.rooms import Room, lead_entry, lock_room
from app.livekit import LiveKitUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/livekit", tags=["rooms"], include_in_schema=False)


def _verify(authorization: str | None, body: bytes) -> None:
    """LiveKit signs each webhook: a JWT in `Authorization` (HS256, the API key as issuer) with a
    `sha256` claim holding the base64 SHA-256 of the exact body. A bad signature, another issuer
    or a body that does not match is a 401."""
    settings = get_settings()
    if not (settings.LIVEKIT_API_KEY and settings.LIVEKIT_API_SECRET):
        raise HTTPException(status_code=503, detail="Audio rooms are not set up on this server")
    token = (authorization or "").removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(
            token,
            settings.LIVEKIT_API_SECRET,
            algorithms=[livekit.ALGORITHM],
            options={"verify_aud": False},
        )
    except JWTError:
        raise HTTPException(status_code=401, detail="Bad signature") from None
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    if claims.get("iss") != settings.LIVEKIT_API_KEY or claims.get("sha256") != digest:
        raise HTTPException(status_code=401, detail="Bad signature")


def _on_join(db: Session, livekit_room: str, identity: str) -> None:
    """Set `identity`'s permission in `livekit_room` to what the database says it should be."""
    found = db.execute(
        select(Room.id).where(Room.livekit_room == livekit_room)
    ).scalar_one_or_none()
    if found is None:
        return  # not a room this gateway made
    room = lock_room(db, found)
    if room is None or room.status != "open":
        db.rollback()
        return
    lead = lead_entry(db, room.id)
    should_publish = bool(lead and str(lead.user_id) == identity)
    admin = livekit.get_livekit_admin()
    if admin is None:
        db.rollback()
        raise HTTPException(status_code=503, detail="Audio rooms are not set up on this server")
    try:
        admin.set_can_publish(room.livekit_room, identity, should_publish)
    except LiveKitUnavailable as exc:
        db.rollback()
        logger.warning("room %s: join check could not reach LiveKit: %s", room.id, exc)
        raise HTTPException(status_code=502, detail="LiveKit is not reachable") from exc
    db.commit()  # ends the transaction and so releases the room lock
    logger.info("room %s: %s joined, may publish: %s", room.id, identity, should_publish)


@router.post("/webhook")
async def livekit_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    body = await request.body()
    _verify(request.headers.get("authorization"), body)
    try:
        event = json.loads(body)
        kind = event.get("event")
        room_name = (event.get("room") or {}).get("name")
        identity = (event.get("participant") or {}).get("identity")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Not an event") from None
    if kind == "participant_joined" and room_name and identity:
        await run_in_threadpool(_on_join, db, room_name, identity)
    return {"ok": True}
