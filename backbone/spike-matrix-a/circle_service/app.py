"""Matrix-backed circle service: the same 6-route HTTP contract as
backbone/spike-custom-lite/app.py's circle routes, backed by a real
Tuwunel homeserver instead of a Postgres outbox.

gateway/backbone_client.py talks to whichever of these two services
BACKBONE_URL points at; the request/response shapes below are matched
exactly so swapping the URL needs no gateway changes. See
backbone/interfaces.py and docs/adr/0002-chat-backbone.md.
"""

import os
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from bootstrap import BootstrapError, bootstrap_appservice
from interfaces import BackboneUnavailable
from matrix_circle_store import MatrixCircleStore

HOMESERVER_URL = os.environ.get("HOMESERVER_URL", "http://tuwunel:8008")
SERVER_NAME = os.environ.get("SERVER_NAME", "localhost")
AS_ID = os.environ.get("AS_ID", "satsandesh-circles-matrix")
AS_TOKEN = os.environ.get("AS_TOKEN", "dev_circles_as_token_change_me")
HS_TOKEN = os.environ.get("HS_TOKEN", "dev_circles_hs_token_change_me")
BOT_LOCALPART = os.environ.get("BOT_LOCALPART", "satsandesh_circles_bot")
ADMIN_USERNAME = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "matrix_bootstrap_admin")
ADMIN_PASSWORD = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "dev_bootstrap_admin_password")

_REGISTRATION_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "registration.yaml")


def _render_registration_yaml() -> str:
    """Substitutes id/as_token/hs_token placeholders with real values.

    Two bugs found running the test suite, same class both times: a
    field was a configurable env var in Python but a plain literal in
    the YAML template, so the actual server-side registration silently
    ignored the env var.

    1. AS_ID: `id:` was hardcoded, so bootstrap's confirmation check
       (which waits for "Appservice registered with ID: {AS_ID}") never
       matched the server's real reply, and always timed out.
    2. BOT_LOCALPART: `sender_localpart:` was hardcoded to
       "satsandesh_circles_bot", so under the test config
       (BOT_LOCALPART=satsandesh_circles_bot_test) the code tried to
       operate as a bot user Tuwunel had never actually registered --
       failing with M_EXCLUSIVE ("Username is not in an appservice
       namespace") the moment create_circle tried to provision it.

    Both fixed by templating those fields the same way as the tokens.
    """
    with open(_REGISTRATION_TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()
    return (
        template.replace("REPLACE_WITH_AS_ID_FROM_ENV", AS_ID)
        .replace("REPLACE_WITH_BOT_LOCALPART_FROM_ENV", BOT_LOCALPART)
        .replace("REPLACE_WITH_AS_TOKEN_FROM_ENV", AS_TOKEN)
        .replace("REPLACE_WITH_HS_TOKEN_FROM_ENV", HS_TOKEN)
    )


store: Optional[MatrixCircleStore] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    try:
        await bootstrap_appservice(
            homeserver_url=HOMESERVER_URL,
            registration_yaml=_render_registration_yaml(),
            appservice_id=AS_ID,
            admin_username=ADMIN_USERNAME,
            admin_password=ADMIN_PASSWORD,
        )
    except BootstrapError as exc:
        # Fail loudly at startup rather than serving requests against an
        # appservice that was never actually registered -- every request
        # would fail anyway, just later and less clearly.
        raise RuntimeError(f"appservice bootstrap failed: {exc}") from exc

    store = MatrixCircleStore(
        homeserver_url=HOMESERVER_URL,
        as_token=AS_TOKEN,
        server_name=SERVER_NAME,
        bot_localpart=BOT_LOCALPART,
    )
    yield


app = FastAPI(title="SatSandesh Circles — Matrix backend", lifespan=lifespan)


def _get_store() -> MatrixCircleStore:
    if store is None:
        raise HTTPException(status_code=503, detail="backbone not ready")
    return store


def _unavailable(exc: BackboneUnavailable) -> HTTPException:
    return HTTPException(status_code=503, detail=f"backbone unavailable: {exc}")


@app.get("/health")
def health():
    return {"status": "ok", "service": "circle-service-matrix-a"}


class CreateCircleRequest(BaseModel):
    name: str


class AddMemberRequest(BaseModel):
    user_id: str


class AnnounceRequest(BaseModel):
    sender_id: str
    body: str


@app.post("/circles")
async def create_circle(req: CreateCircleRequest):
    try:
        circle_id = await _get_store().create_circle(req.name)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"circle_id": circle_id}


@app.post("/circles/{circle_id}/members")
async def add_member(circle_id: str, req: AddMemberRequest):
    try:
        await _get_store().add_member(circle_id, req.user_id)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"status": "ok"}


@app.delete("/circles/{circle_id}/members/{user_id}")
async def remove_member(circle_id: str, user_id: str):
    try:
        await _get_store().remove_member(circle_id, user_id)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"status": "ok"}


@app.get("/circles/{circle_id}/members")
async def list_members(circle_id: str):
    try:
        members: List[str] = await _get_store().list_members(circle_id)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"members": members}


@app.post("/circles/{circle_id}/announce")
async def announce(circle_id: str, req: AnnounceRequest):
    try:
        message_id = await _get_store().post_announcement(circle_id, req.sender_id, req.body)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
    return {"message_id": message_id}


@app.get("/circles/{circle_id}/messages")
async def list_messages(circle_id: str, limit: int = 50, before: Optional[str] = None):
    try:
        messages = await _get_store().list_messages(circle_id, limit=limit, before=before)
    except BackboneUnavailable as exc:
        raise _unavailable(exc)
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
