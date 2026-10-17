"""Phase 3a: the moderator routes accept a SIGNED token only, in every AUTH_MODE.

Why (OPEN_QUESTIONS #23): in the default `AUTH_MODE=legacy` a bare UUID bearer is "that user", with
no signature, so anyone who knows a moderator's user id could read the queue, see a held message's
text and release or block it. Caddy cannot know the gateway's mode, so the gateway itself must
refuse: a request to /moderation/* is authenticated only by a verified signed token (app/tokens.py),
whatever mode the rest of the app runs in. Signed tokens are already verified strictly in both modes,
so this does not need `AUTH_MODE=jwt` and does not lock anyone out of the elder app.

Found by hand first: on a real stack (AUTH_MODE=legacy), `Authorization: Bearer <moderator uuid>`
on GET /moderation/queue returned 200 with the held message's text.
"""

import uuid

import pytest

from app.config import get_settings
from app.db.models import User as DbUser
from app.tokens import issue_token


def _user(db_session, name, role):
    user = DbUser(name=name, preferred_language="te", role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


ROUTES = [
    ("GET", "/moderation/queue", None),
    ("GET", f"/moderation/messages/{uuid.uuid4()}/events", None),
    ("POST", f"/moderation/messages/{uuid.uuid4()}/release", {}),
    ("POST", f"/moderation/messages/{uuid.uuid4()}/block", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_bare_uuid_is_not_a_moderator_even_when_that_user_is_one(
    client, db_session, method, path, body
):
    assert get_settings().AUTH_MODE == "legacy"  # the default: this is the exposed configuration
    mod = _user(db_session, "Mod", "moderator")

    resp = client.request(method, path, headers=_hdr(mod.id), json=body)

    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "Moderation requires a signed token"}


def test_a_bare_uuid_on_a_moderation_route_provisions_no_user(client, db_session):
    """The legacy stub creates a users row for any UUID it sees; a refused request must not."""
    stranger = uuid.uuid4()

    resp = client.get("/moderation/queue", headers=_hdr(stranger))

    assert resp.status_code == 401
    assert db_session.get(DbUser, stranger) is None


def test_a_non_uuid_junk_token_is_refused_the_same_way(client):
    resp = client.get("/moderation/queue", headers=_hdr("abc"))

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Moderation requires a signed token"}


def test_no_token_is_still_not_authenticated(client):
    resp = client.get("/moderation/queue")

    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


def test_a_signed_moderator_token_works_in_legacy_mode(client, db_session):
    mod = _user(db_session, "Mod", "moderator")

    resp = client.get("/moderation/queue", headers=_hdr(issue_token(mod.id)))

    assert resp.status_code == 200, resp.text


def test_a_signed_elder_token_is_403(client, db_session):
    elder = _user(db_session, "Elder", "elder")

    resp = client.get("/moderation/queue", headers=_hdr(issue_token(elder.id)))

    assert resp.status_code == 403
    assert resp.json() == {"detail": "Insufficient role"}


def test_the_role_is_the_databases_not_the_tokens(client, db_session):
    """A signed token has no role claim: demoting the user takes effect on the next request."""
    mod = _user(db_session, "Mod", "moderator")
    token = issue_token(mod.id)
    assert client.get("/moderation/queue", headers=_hdr(token)).status_code == 200

    mod.role = "elder"
    db_session.commit()

    assert client.get("/moderation/queue", headers=_hdr(token)).status_code == 403


def test_a_refusal_is_decided_before_any_lookup(client, db_session):
    """A bare-UUID refusal is the same for any message id: nothing about the message is consulted."""
    mod = _user(db_session, "Mod", "moderator")
    for message_id in (uuid.uuid4(), uuid.uuid4()):
        resp = client.get(f"/moderation/messages/{message_id}/events", headers=_hdr(mod.id))
        assert resp.status_code == 401
        assert resp.json() == {"detail": "Moderation requires a signed token"}
