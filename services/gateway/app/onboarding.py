"""Family-assisted QR onboarding.

Flow:
  1. Family member (with a valid session token) calls POST /onboarding/invite
     with the elder's display name, phone (optional), and preferred language.
     Receives a signed, time-limited invite token.
  2. Family member calls GET /onboarding/qr/{invite_token} to receive a QR
     code PNG.  They show or print it for the elder.
  3. Elder's app scans the QR.  The app POSTs the raw token to
     POST /onboarding/activate and receives their UUID as a session token —
     no typing required on the elder's side.

Invite tokens expire after INVITE_TOKEN_TTL_SECONDS (default 7 days) and
are single-use: the first successful /activate call consumes the invite.

Token format:
  <invite_id>.<expires_at_unix>.<hmac_sha256_hex>
  invite_id is a URL-safe random string (no dots), expires_at is an int,
  hmac is hex — splitting on "." with maxsplit=2 is unambiguous.

Storage: a Postgres `invites` table (app.db.models.Invite), one row per
pending invite, deleted on activation. Previously an in-memory dict —
switched after PR #29's review (kpspyolo024) found a killed-and-restarted
gateway silently dropped every pending invite, including ones already
shown to an elder as a QR code.
"""

import hashlib
import hmac
import io
import os
import secrets
import time
import uuid
from datetime import UTC, datetime

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db.base import get_db
from app.db.models import Invite
from app.db.models import User as DbUser
from app.models import User

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

INVITE_TOKEN_TTL_SECONDS = int(os.environ.get("INVITE_TOKEN_TTL_SECONDS", str(7 * 24 * 60 * 60)))


# ---------------------------------------------------------------------------
# Invite token helpers
# ---------------------------------------------------------------------------


def _sign(invite_id: str, expires_at: int) -> str:
    secret = get_settings().JWT_SECRET
    payload = f"{invite_id}:{expires_at}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _issue_invite_token(
    db: Session, display_name: str, phone: str, language: str, invited_by: uuid.UUID
) -> tuple[str, int]:
    """Creates a pending invite row and returns (invite_token, expires_at)."""
    invite_id = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + INVITE_TOKEN_TTL_SECONDS
    sig = _sign(invite_id, expires_at)
    invite_token = f"{invite_id}.{expires_at}.{sig}"

    db.add(
        Invite(
            id=invite_id,
            display_name=display_name,
            phone=phone,
            language=language,
            invited_by=invited_by,
            expires_at=datetime.fromtimestamp(expires_at, tz=UTC),
        )
    )
    db.commit()

    return invite_token, expires_at


def _verify_invite_token(token: str) -> str:
    """Returns invite_id, or raises HTTPException 400/410."""
    try:
        invite_id, expires_at_str, sig = token.split(".", 2)
        expires_at = int(expires_at_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="malformed invite token")

    expected = _sign(invite_id, expires_at)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=400, detail="invalid invite token")
    if time.time() > expires_at:
        raise HTTPException(status_code=410, detail="invite token expired")

    return invite_id


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class InviteRequest(BaseModel):
    display_name: str
    phone: str = ""
    language: str = "te"


class InviteResponse(BaseModel):
    invite_token: str
    expires_at: int


class ActivateRequest(BaseModel):
    invite_token: str


class ActivateResponse(BaseModel):
    token: str  # the elder's UUID — use as Bearer token on subsequent requests
    user_id: str
    display_name: str
    language: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/invite", response_model=InviteResponse, status_code=201)
def create_invite(
    req: InviteRequest,
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteResponse:
    """Family member creates an invite for an elder.

    Requires a valid session token (Authorization: Bearer <token>).
    Returns an invite token the family member exchanges for a QR code.
    """
    invite_token, expires_at = _issue_invite_token(
        db, req.display_name, req.phone, req.language, uuid.UUID(caller.id)
    )
    return InviteResponse(invite_token=invite_token, expires_at=expires_at)


@router.get("/qr/{invite_token}")
def get_qr_code(invite_token: str) -> Response:
    """Returns a PNG QR code for the given invite token.

    The QR encodes the raw invite token string.  The elder's app scans it
    and POSTs the value to POST /onboarding/activate.

    No auth required — the family member shares this image directly.
    Validates the token first so expired/tampered tokens are rejected before
    wasting time generating an image.
    """
    _verify_invite_token(invite_token)

    img = qrcode.make(invite_token)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@router.post("/activate", response_model=ActivateResponse)
def activate_invite(
    req: ActivateRequest,
    db: Session = Depends(get_db),
) -> ActivateResponse:
    """Elder's app posts the scanned invite token; receives their UUID as a session token.

    Single-use: the invite is consumed on the first successful call.
    Subsequent calls with the same token return 410.

    Consuming the invite is one DELETE ... RETURNING statement rather than
    a separate SELECT-then-DELETE, so single-use holds even if two
    /activate calls for the same token race each other — Postgres's row
    lock during the DELETE means at most one of them gets a row back, the
    same guarantee the old in-memory dict.pop had within a single process,
    now also true across multiple gateway instances sharing one database.

    RETURNING only the two specific columns needed (not the whole Invite
    entity) on purpose. .returning(Invite) plus .scalars() hands back an
    Invite ORM instance bound to this Session, and accessing its
    attributes after the db.commit() below re-triggers a SELECT under
    expire-on-commit — the default for a bare Session(), which is what
    tests/conftest.py's db_session fixture uses (prod's own SessionLocal
    sets expire_on_commit=False, so this only broke under tests). That
    refresh finds nothing, since the row was just deleted, and raises
    ObjectDeletedError. .returning(Invite) plus .mappings() doesn't fix
    it either — the RowMapping keys on the ORM entity, not plain column
    names, so `invite["display_name"]` raises KeyError. Selecting the two
    columns directly gives a plain Row of scalars with no ORM instance
    involved at all, sidestepping both problems (found by running the
    suite against real Postgres, not by reasoning about SQLAlchemy's
    RETURNING semantics in the abstract).
    """
    invite_id = _verify_invite_token(req.invite_token)

    result = db.execute(
        delete(Invite).where(Invite.id == invite_id).returning(Invite.display_name, Invite.language)
    )
    row = result.one_or_none()
    db.commit()
    if row is None:
        raise HTTPException(status_code=410, detail="invite already used or not found")
    display_name, language = row

    user = DbUser(
        name=display_name,
        preferred_language=language,
        role="elder",
    )
    db.add(user)
    db.commit()

    user_id = str(user.id)
    return ActivateResponse(
        token=user_id,
        user_id=user_id,
        display_name=display_name,
        language=language,
    )
