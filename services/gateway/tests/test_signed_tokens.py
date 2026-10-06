"""Signed session tokens (Week 8): what replaces "any UUID is that user".

Two steps, one switch. Step 1 (this change) builds the signed token and its strict
verification and puts it behind `AUTH_MODE`: `legacy` (default) keeps the UUID stub working so
nothing existing breaks; `jwt` accepts ONLY a valid signed token. Step 2 (not here) is turning
`jwt` on for staging once the elder app can obtain a token.

Rules these tests pin:
  * a token that LOOKS like a JWT (three dot-separated parts) is verified strictly in BOTH
    modes: a bad one is a 401 and never falls back to the stub identity
  * every bad token is a 401, never a 500
  * a valid token is that user and only that user; a signature for an unknown user is a 401 and
    creates nothing (the stub provisioned rows; real auth must not)
  * the role comes from the database, so a valid token is NOT a license to reach moderator routes
  * the author/recipient media rules and the held-audio rule are unchanged under real tokens
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt as jose_jwt
from starlette.websockets import WebSocketDisconnect

from app import tokens
from app.auth import user_from_token
from app.config import get_settings
from app.db.models import User as DbUser
from app.db.repository import create_media_object, create_message, set_message_status
from app.media_storage import get_media_storage

SECRET_FIELD = "JWT_SECRET"


@pytest.fixture
def jwt_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "AUTH_MODE", "jwt")


def _user(db_session, name="Someone", *, role="elder", language="te"):
    user = DbUser(name=name, preferred_language=language, role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _claims(user_id, **over):
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iss": tokens.ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


def _sign(claims, key=None, alg="HS256"):
    return jose_jwt.encode(claims, key or get_settings().JWT_SECRET, algorithm=alg)


# -- every bad token is a 401, in strict mode ------------------------------------------------


def _bad_tokens(user_id):
    secret = get_settings().JWT_SECRET
    return {
        "garbage that is not a token": "not-a-token",
        "a bare UUID (the old stub) in strict mode": str(user_id),
        "three parts but not base64 JSON": "abc.def.ghi",
        "expired": tokens.issue_token(user_id, now=datetime.now(UTC) - timedelta(days=60)),
        "signed with the wrong key": _sign(_claims(user_id), key="not-the-gateway-secret"),
        "alg none (unsigned)": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims(user_id))}.",
        "right key, wrong algorithm (HS512)": _sign(_claims(user_id), key=secret, alg="HS512"),
        "no expiry claim": _sign(_claims(user_id, exp=None)),
        "no subject": _sign(_claims(user_id, sub=None)),
        "subject is not a UUID": _sign(_claims(user_id, sub="alice")),
        "wrong issuer": _sign(_claims(user_id, iss="someone-else")),
        "tampered payload": _tamper(tokens.issue_token(user_id)),
    }


def _tamper(token):
    head, payload, sig = token.split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged["sub"] = str(uuid.uuid4())
    return f"{head}.{_b64(forged)}.{sig}"


def test_every_kind_of_bad_token_is_a_401_never_a_500(client, db_session, jwt_mode):
    user = _user(db_session)

    for label, token in _bad_tokens(user.id).items():
        resp = client.get("/me/settings", headers=_hdr(token))
        assert resp.status_code == 401, f"{label}: expected 401, got {resp.status_code}"


def test_no_token_is_a_401(client, db_session, jwt_mode):
    assert client.get("/me/settings").status_code == 401


def test_a_valid_signature_for_a_user_who_does_not_exist_is_a_401_and_creates_nothing(
    client, db_session, jwt_mode
):
    ghost = uuid.uuid4()

    resp = client.get("/me/settings", headers=_hdr(tokens.issue_token(ghost)))

    assert resp.status_code == 401
    db_session.expire_all()
    assert db_session.get(DbUser, ghost) is None, "the stub provisioned rows; real auth must not"


def test_a_jwt_shaped_bad_token_is_refused_even_in_legacy_mode_not_given_the_stub_identity(
    client, db_session
):
    # AUTH_MODE is the default (legacy) here. "a.b.c" used to become the fixed stub-user-1.
    resp = client.get("/me/settings", headers=_hdr("a.b.c"))

    assert resp.status_code == 401


# -- a valid token is that user and only that user ---------------------------------------------


def test_a_valid_token_is_that_user_and_only_that_user(client, db_session, jwt_mode):
    alice = _user(db_session, "Alice", language="hi")
    bob = _user(db_session, "Bob", language="en")

    as_alice = client.get("/me/settings", headers=_hdr(tokens.issue_token(alice.id)))
    as_bob = client.get("/me/settings", headers=_hdr(tokens.issue_token(bob.id)))

    assert as_alice.status_code == 200 and as_alice.json()["preferred_language"] == "hi"
    assert as_bob.status_code == 200 and as_bob.json()["preferred_language"] == "en"


def test_a_write_with_one_users_token_changes_only_that_user(client, db_session, jwt_mode):
    alice = _user(db_session, "Alice", language="hi")
    bob = _user(db_session, "Bob", language="en")

    resp = client.patch(
        "/me/settings",
        headers=_hdr(tokens.issue_token(alice.id)),
        json={"preferred_language": "te"},
    )

    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.get(DbUser, alice.id).preferred_language == "te"
    assert db_session.get(DbUser, bob.id).preferred_language == "en"


def test_the_role_comes_from_the_database_not_the_token(db_session, jwt_mode):
    moderator = _user(db_session, "Mod", role="moderator")

    wire = user_from_token(tokens.issue_token(moderator.id), db_session)

    assert wire.id == str(moderator.id) and wire.role == "moderator"


def test_user_from_token_raises_401_for_a_bad_token(db_session, jwt_mode):
    with pytest.raises(HTTPException) as err:
        user_from_token("a.b.c", db_session)

    assert err.value.status_code == 401


# -- the switch --------------------------------------------------------------------------------


def test_legacy_mode_still_accepts_a_bare_uuid_and_strict_mode_does_not(
    client, db_session, monkeypatch
):
    user = _user(db_session)

    assert client.get("/me/settings", headers=_hdr(str(user.id))).status_code == 200  # legacy

    monkeypatch.setattr(get_settings(), "AUTH_MODE", "jwt")
    assert client.get("/me/settings", headers=_hdr(str(user.id))).status_code == 401  # strict


def test_a_valid_signed_token_works_in_legacy_mode_too(client, db_session):
    user = _user(db_session)

    resp = client.get("/me/settings", headers=_hdr(tokens.issue_token(user.id)))

    assert resp.status_code == 200


# -- role, not just identity -------------------------------------------------------------------


def test_a_valid_token_for_an_elder_cannot_reach_the_moderator_routes(client, db_session, jwt_mode):
    elder = _user(db_session, "Elder")
    moderator = _user(db_session, "Mod", role="moderator")

    assert (
        client.get("/moderation/queue", headers=_hdr(tokens.issue_token(elder.id))).status_code
        == 403
    )
    assert (
        client.get("/moderation/queue", headers=_hdr(tokens.issue_token(moderator.id))).status_code
        == 200
    )


# -- media rules unchanged, under real tokens --------------------------------------------------


def _voice(db_session, *, author, target, status):
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author.id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=1000,
    )
    db_session.commit()
    media_id = media.id
    get_media_storage().put(str(media_id), b"the-note")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="voice",
        original_media_ref=f"media:{media_id}",
        media_duration_ms=1000,
        media_object_id=media_id,
        media_format="wav_pcm16",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    message_id = message.id
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return media_id


def _fetch(client, media_id, user):
    return client.get(f"/media/{media_id}", headers=_hdr(tokens.issue_token(user.id))).status_code


def test_the_media_download_rules_are_unchanged_under_real_tokens(client, db_session, jwt_mode):
    author = _user(db_session, "Author")
    recipient = _user(db_session, "Recipient")
    outsider = _user(db_session, "Outsider")
    moderator = _user(db_session, "Mod", role="moderator")
    delivered = _voice(db_session, author=author, target=recipient, status="delivered")
    held = _voice(db_session, author=author, target=recipient, status="held")
    pending = _voice(db_session, author=author, target=recipient, status="pending")

    # the author always; a recipient of a delivered message; nobody else
    assert _fetch(client, delivered, author) == 200
    assert _fetch(client, delivered, recipient) == 200
    assert _fetch(client, delivered, outsider) == 404
    # a held message: author and a moderator only (#14, the global-role rule, not widened)
    assert _fetch(client, held, author) == 200
    assert _fetch(client, held, moderator) == 200
    assert _fetch(client, held, recipient) == 404, "the read-path leak (#25) must not return"
    assert _fetch(client, held, outsider) == 404
    # a pending message (inside the undo window): author only, not even a moderator
    assert _fetch(client, pending, author) == 200
    assert _fetch(client, pending, moderator) == 404
    assert _fetch(client, pending, recipient) == 404


# -- the WebSocket ---------------------------------------------------------------------------


def test_a_websocket_with_a_bad_token_is_closed_1008_and_a_good_one_connects(
    client, db_session, jwt_mode
):
    user = _user(db_session)

    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect("/ws?token=a.b.c") as ws:
            ws.receive_text()
    assert err.value.code == 1008

    with client.websocket_connect(f"/ws?token={tokens.issue_token(user.id)}"):
        pass  # accepted: no disconnect raised


# -- issuing -----------------------------------------------------------------------------------


def test_onboarding_activation_issues_a_signed_token_and_keeps_the_legacy_one(
    client, db_session, login_as
):
    inviter = _user(db_session, "Family", role="elder")
    login_as(inviter)
    invite = client.post("/onboarding/invite", json={"display_name": "Nani"}).json()["invite_token"]

    body = client.post("/onboarding/activate", json={"invite_token": invite}).json()

    assert body["token"] == body["user_id"], "additive: the legacy field is unchanged"
    assert str(tokens.verify_token(body["access_token"])) == body["user_id"]


def test_the_issue_tool_prints_a_token_that_verifies(capsys):
    who = uuid.uuid4()

    tokens.main([str(who)])

    printed = capsys.readouterr().out.strip()
    assert tokens.verify_token(printed) == who
