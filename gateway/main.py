import os

import circles
import psycopg
import ws
from auth import issue_token
from backbone_client import HttpCircleBackbone
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="SatSandesh Gateway")

DATABASE_URL = os.environ.get("DATABASE_URL")

# Explicit origins, never "*" -- Week 4's task calls this out specifically
# ("this WILL fail silently as a blocked browser request the first time
# you test it if skipped"), confirmed true: the elder client's requests
# were silently rejected by the browser (not even reaching this process's
# logs) until this was configured, during this week's own local testing.
# Comma-separated so both a local dev origin and a staging IP can be
# listed without a code change.
_allowed_origins = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The one place the gateway names a concrete backbone. Everything else --
# circles.py in particular -- sees only the CircleBackbone interface, so
# resolving ADR 0002 means changing this line and BACKBONE_URL, not the
# route code.
circles.set_backbone(HttpCircleBackbone())
app.include_router(circles.router)


class CreateSessionRequest(BaseModel):
    display_name: str


@app.post("/session")
def create_session(req: CreateSessionRequest):
    """Issues a session token. Not the final auth system (see auth.py's
    module docstring) -- a display name is all that's asked for -- but
    from here on, `who sent this` is a verified token claim, not a bare
    string a client can assert fresh on every message."""
    token, user_id = issue_token(req.display_name)
    return {"token": token, "user_id": user_id}


@app.websocket("/ws")
async def ws_route(websocket: WebSocket, token: str):
    await ws.websocket_endpoint(websocket, token, circles.get_backbone)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-check")
def db_check():
    """Confirms Postgres is reachable and db/init/001_init.sql ran."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT note FROM schema_check ORDER BY id LIMIT 1;")
                row = cur.fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}")

    if row is None:
        raise HTTPException(
            status_code=500, detail="schema_check table is empty — init did not run"
        )

    return {"status": "ok", "schema_check": row[0]}
