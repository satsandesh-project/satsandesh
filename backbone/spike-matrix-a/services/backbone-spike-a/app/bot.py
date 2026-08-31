"""
Spike A: minimal Matrix Application Service bot.

Proves the AS transaction-push mechanism works — a homeserver PUTs events
to us, we query which users/rooms we own. No moderation/translation logic
here; that's out of scope for this spike. State is in-memory only.
"""

import logging
import os
import re

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

logger = logging.getLogger("spike_a_bot")

HS_TOKEN = os.environ.get("HS_TOKEN", "")
AS_TOKEN = os.environ.get("AS_TOKEN", "")
USER_NAMESPACE_REGEX = re.compile(os.environ.get("USER_NAMESPACE_REGEX", r"@spike_.*:localhost"))
ROOM_ALIAS_NAMESPACE_REGEX = re.compile(
    os.environ.get("ROOM_ALIAS_NAMESPACE_REGEX", r"#spike_.*:localhost")
)
BOT_USER_ID = os.environ.get("BOT_USER_ID", "@satsandesh_bot:localhost")
HOMESERVER_URL = os.environ.get("HOMESERVER_URL", "http://localhost:6167")
# The AS's own server domain, derived from its own user ID rather than
# hardcoded, so it stays correct if BOT_USER_ID's domain ever changes.
HOMESERVER_DOMAIN = BOT_USER_ID.rsplit(":", 1)[-1]

app = FastAPI(title="SatSandesh Spike A - AS Bot")

received_events: list[dict] = []
_processed_txn_ids: set[str] = set()


def _require_hs_token(authorization: str | None) -> None:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"errcode": "M_MISSING_TOKEN", "error": "Missing Authorization header"},
        )
    token = authorization.removeprefix("Bearer ")
    if token != HS_TOKEN:
        raise HTTPException(
            status_code=401, detail={"errcode": "M_UNKNOWN_TOKEN", "error": "Bad token"}
        )


def _fully_qualified_room_id(room_id: str) -> str:
    # Transaction payloads from this Conduit build have been observed to
    # omit the server-name suffix (e.g. "!abc" instead of "!abc:localhost").
    # The join API needs the full form, so qualify it if it's missing.
    return room_id if ":" in room_id else f"{room_id}:{HOMESERVER_DOMAIN}"


async def _join_room_as_bot(room_id: str) -> None:
    # A homeserver only forwards message events for rooms the AS's own user
    # has *joined* -- being invited is not enough. Accept on the bot's
    # behalf using the AS's own token (impersonating its registered
    # sender_localpart, per the AS spec), not a human login.
    qualified_room_id = _fully_qualified_room_id(room_id)
    url = f"{HOMESERVER_URL}/_matrix/client/v3/rooms/{qualified_room_id}/join"
    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(url, headers={"Authorization": f"Bearer {AS_TOKEN}"})
        logger.info(
            "join attempt room_id=%s -> HTTP %s: %s",
            qualified_room_id,
            resp.status_code,
            resp.text,
        )
        resp.raise_for_status()


@app.put("/_matrix/app/v1/transactions/{txn_id}")
async def receive_transaction(
    txn_id: str, request: Request, authorization: str | None = Header(default=None)
):
    _require_hs_token(authorization)

    # Homeservers may retry a transaction id if they didn't see our 200 in
    # time; the spec requires us not to double-process it.
    if txn_id in _processed_txn_ids:
        return JSONResponse({})

    body = await request.json()
    for event in body.get("events", []):
        received_events.append(event)

        is_invite_for_bot = (
            event.get("type") == "m.room.member"
            and event.get("state_key") == BOT_USER_ID
            and event.get("content", {}).get("membership") == "invite"
        )
        if is_invite_for_bot:
            # Joining is a best-effort side action; a failure here (e.g. a
            # homeserver-side bug) must not stop us from acknowledging
            # receipt of the event itself, or the homeserver will retry
            # this transaction forever.
            try:
                await _join_room_as_bot(event["room_id"])
            except httpx.HTTPStatusError as exc:
                logger.warning("failed to join room %s: %s", event["room_id"], exc)

    _processed_txn_ids.add(txn_id)

    return JSONResponse({})


@app.get("/received")
async def get_received_events():
    return received_events


@app.post("/_matrix/app/unstable/org.matrix.msc3984/keys/query")
async def proxy_keys_query(authorization: str | None = Header(default=None)):
    # MSC3984: homeserver proxies E2EE key queries through the AS. We don't
    # handle E2EE (see SPIKE_A_FINDINGS.md), so an empty response is
    # correct -- we have no keys to report. Per the MSC, homeservers should
    # already treat any AS error here as equivalent to {}; this just makes
    # it explicit rather than relying on that fallback.
    _require_hs_token(authorization)
    return JSONResponse({"device_keys": {}, "master_keys": {}, "self_signing_keys": {}})


@app.get("/_matrix/app/v1/users/{user_id}")
async def query_user(user_id: str, authorization: str | None = Header(default=None)):
    _require_hs_token(authorization)
    if USER_NAMESPACE_REGEX.fullmatch(user_id):
        return JSONResponse({})
    raise HTTPException(
        status_code=404, detail={"errcode": "M_NOT_FOUND", "error": "User not known to this AS"}
    )


@app.get("/_matrix/app/v1/rooms/{room_alias}")
async def query_room(room_alias: str, authorization: str | None = Header(default=None)):
    _require_hs_token(authorization)
    if ROOM_ALIAS_NAMESPACE_REGEX.fullmatch(room_alias):
        return JSONResponse({})
    raise HTTPException(
        status_code=404, detail={"errcode": "M_NOT_FOUND", "error": "Room not known to this AS"}
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "9000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
