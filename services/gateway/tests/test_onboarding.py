"""Tests for the family-assisted QR onboarding endpoints.

Full happy-path: POST /onboarding/invite -> GET /onboarding/qr/{token}
-> POST /onboarding/activate.  Plus error cases for auth, tampered tokens,
expiry, and double-use.

These tests use the conftest.py client/login_as/db_session fixtures and
therefore require a real Postgres test database (see services/gateway/README.md).
The activate endpoint inserts a real `users` row, so a live DB is necessary.
"""

import time
import uuid

import pytest

from app import onboarding
from app.db.models import User as DbUser


@pytest.fixture(autouse=True)
def clear_pending():
    """Reset in-memory invite store between tests."""
    onboarding._pending.clear()
    yield
    onboarding._pending.clear()


def _make_inviter(db_session, name="Family Member"):
    user = DbUser(name=name, preferred_language="en", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# POST /onboarding/invite
# ---------------------------------------------------------------------------


def test_invite_returns_token_and_expires(client, db_session, login_as):
    inviter = _make_inviter(db_session)
    login_as(inviter)

    resp = client.post(
        "/onboarding/invite",
        json={"display_name": "Radha Bai", "phone": "+919876543210", "language": "te"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert "invite_token" in body
    assert body["expires_at"] > int(time.time())


def test_invite_defaults_phone_and_language(client, db_session, login_as):
    inviter = _make_inviter(db_session)
    login_as(inviter)

    resp = client.post("/onboarding/invite", json={"display_name": "Sita"})

    assert resp.status_code == 201
    body = resp.json()
    invite_id = body["invite_token"].split(".")[0]
    assert onboarding._pending[invite_id]["language"] == "te"
    assert onboarding._pending[invite_id]["phone"] == ""


def test_invite_stores_inviter_id(client, db_session, login_as):
    inviter = _make_inviter(db_session, "Priya")
    wire_user = login_as(inviter)

    resp = client.post("/onboarding/invite", json={"display_name": "Amma"})

    assert resp.status_code == 201
    invite_id = resp.json()["invite_token"].split(".")[0]
    assert onboarding._pending[invite_id]["invited_by"] == wire_user.id


def test_invite_requires_auth(client, db_session):
    resp = client.post("/onboarding/invite", json={"display_name": "Ramu"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /onboarding/qr/{invite_token}
# ---------------------------------------------------------------------------


def test_qr_returns_png(client, db_session, login_as):
    inviter = _make_inviter(db_session, "Appa")
    login_as(inviter)
    resp = client.post("/onboarding/invite", json={"display_name": "Thatha"})
    invite_token = resp.json()["invite_token"]

    qr_resp = client.get(f"/onboarding/qr/{invite_token}")

    assert qr_resp.status_code == 200
    assert qr_resp.headers["content-type"] == "image/png"
    assert qr_resp.content[:4] == b"\x89PNG"


def test_qr_rejects_tampered_token(client, db_session, login_as):
    inviter = _make_inviter(db_session)
    login_as(inviter)
    resp = client.post("/onboarding/invite", json={"display_name": "Nani"})
    token = resp.json()["invite_token"]
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")

    qr_resp = client.get(f"/onboarding/qr/{tampered}")

    assert qr_resp.status_code == 400


def test_qr_rejects_expired_token(client, db_session, login_as, monkeypatch):
    monkeypatch.setattr(onboarding, "INVITE_TOKEN_TTL_SECONDS", -1)
    inviter = _make_inviter(db_session)
    login_as(inviter)
    resp = client.post("/onboarding/invite", json={"display_name": "Nani"})
    invite_token = resp.json()["invite_token"]

    qr_resp = client.get(f"/onboarding/qr/{invite_token}")

    assert qr_resp.status_code == 410


# ---------------------------------------------------------------------------
# POST /onboarding/activate
# ---------------------------------------------------------------------------


def test_activate_happy_path(client, db_session, login_as):
    inviter = _make_inviter(db_session)
    login_as(inviter)
    resp = client.post(
        "/onboarding/invite",
        json={"display_name": "Lakshmi Devi", "language": "hi"},
    )
    invite_token = resp.json()["invite_token"]

    act = client.post("/onboarding/activate", json={"invite_token": invite_token})

    assert act.status_code == 200
    body = act.json()
    assert body["display_name"] == "Lakshmi Devi"
    assert body["language"] == "hi"
    assert body["token"] == body["user_id"]
    # token must be a valid UUID (usable as Bearer token with the UUID-stub auth)
    uuid.UUID(body["token"])
    # a real users row must exist
    db_user = db_session.get(DbUser, uuid.UUID(body["user_id"]))
    assert db_user is not None
    assert db_user.name == "Lakshmi Devi"
    assert db_user.preferred_language == "hi"
    assert db_user.role == "elder"


def test_activate_is_single_use(client, db_session, login_as):
    inviter = _make_inviter(db_session)
    login_as(inviter)
    resp = client.post("/onboarding/invite", json={"display_name": "Gopal"})
    invite_token = resp.json()["invite_token"]

    first = client.post("/onboarding/activate", json={"invite_token": invite_token})
    assert first.status_code == 200

    second = client.post("/onboarding/activate", json={"invite_token": invite_token})
    assert second.status_code == 410


def test_activate_rejects_tampered_token(client, db_session, login_as):
    inviter = _make_inviter(db_session)
    login_as(inviter)
    resp = client.post("/onboarding/invite", json={"display_name": "Kamala"})
    token = resp.json()["invite_token"]
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")

    act = client.post("/onboarding/activate", json={"invite_token": tampered})

    assert act.status_code == 400


def test_activate_rejects_expired_token(client, db_session, login_as, monkeypatch):
    monkeypatch.setattr(onboarding, "INVITE_TOKEN_TTL_SECONDS", -1)
    inviter = _make_inviter(db_session)
    login_as(inviter)
    resp = client.post("/onboarding/invite", json={"display_name": "Vimala"})
    invite_token = resp.json()["invite_token"]

    act = client.post("/onboarding/activate", json={"invite_token": invite_token})

    assert act.status_code == 410
