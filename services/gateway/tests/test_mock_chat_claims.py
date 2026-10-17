"""The sign-in claim in the mock chat gateway: what the console and the elder app
build against. Same rules as the real routes will have -- a code works once,
unknown / used / voided / expired all read the same, a newer claim voids an
unredeemed older one, and the code never reaches the audit log."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from contracts.chat.admin_org import AdminActionsOut, AdminClaimOut, AdminUserCreated
from contracts.chat.claims import ClaimRedeemedOut
from contracts.chat.mock import admin_org as mock_admin
from contracts.chat.mock import app as mock_app
from contracts.chat.mock import claims as mock_claims
from fastapi.testclient import TestClient

ADMIN = {"X-Mock-Role": "admin", "X-Mock-User-Id": "admin-1"}
GONE = mock_claims.GONE


@pytest.fixture
def client():
    with TestClient(mock_app.app) as test_client:
        yield test_client
    mock_admin._users.clear()
    mock_admin._actions.clear()
    mock_admin._published.clear()
    mock_claims.reset()
    mock_app._circles.clear()
    mock_app._memberships.clear()
    mock_app._messages.clear()


def _person(client, name="Kamala Devi", language="hi") -> AdminUserCreated:
    resp = client.post(
        "/admin/users", headers=ADMIN, json={"name": name, "preferred_language": language}
    )
    return AdminUserCreated.model_validate(resp.json())


def _issue(client, user_id) -> AdminClaimOut:
    resp = client.post(f"/admin/users/{user_id}/claim", headers=ADMIN)
    assert resp.status_code == 201, resp.text
    return AdminClaimOut.model_validate(resp.json())


def _redeem(client, code):
    return client.post("/onboarding/claim", json={"claim_code": code})


def _expire(code) -> None:
    mock_claims._claims[code].expires_at = datetime.now(UTC) - timedelta(seconds=1)


@pytest.mark.parametrize("role", [None, "elder", "moderator"])
def test_only_an_admin_may_issue_a_claim(client, role) -> None:
    person = _person(client).user
    headers = {"X-Mock-Role": role} if role else {}
    assert client.post(f"/admin/users/{person.id}/claim", headers=headers).status_code == 403
    assert mock_claims._claims == {}


def test_issuing_for_an_unknown_person_is_404(client) -> None:
    assert client.post(f"/admin/users/{uuid.uuid4()}/claim", headers=ADMIN).status_code == 404


def test_a_claim_is_issued_with_a_week_to_use_it(client) -> None:
    person = _person(client).user
    claim = _issue(client, person.id)
    assert claim.user_id == person.id and len(claim.claim_code) == 43
    left = claim.expires_at - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < left <= timedelta(days=7)


def test_redeeming_gives_that_persons_session_token_and_language(client) -> None:
    person = _person(client, "Kamala Devi", "hi").user
    code = _issue(client, person.id).claim_code

    resp = _redeem(client, code)

    assert resp.status_code == 200
    out = ClaimRedeemedOut.model_validate(resp.json())
    assert (out.user_id, out.display_name, out.language) == (person.id, "Kamala Devi", "hi")
    assert out.access_token


def test_redeeming_needs_no_authentication(client) -> None:
    code = _issue(client, _person(client).user.id).claim_code
    assert _redeem(client, code).status_code == 200  # no headers at all


def test_a_code_works_once(client) -> None:
    code = _issue(client, _person(client).user.id).claim_code
    assert _redeem(client, code).status_code == 200
    again = _redeem(client, code)
    assert again.status_code == 410 and again.json()["detail"] == GONE


def test_a_newer_claim_voids_an_unredeemed_older_one(client) -> None:
    person = _person(client).user
    old = _issue(client, person.id).claim_code
    new = _issue(client, person.id).claim_code
    assert _redeem(client, old).status_code == 410
    assert _redeem(client, new).status_code == 200


def test_a_redeemed_claim_stays_single_use_when_a_later_one_is_issued(client) -> None:
    person = _person(client).user
    first = _issue(client, person.id).claim_code
    assert _redeem(client, first).status_code == 200
    _issue(client, person.id)
    assert _redeem(client, first).status_code == 410


def test_one_persons_claim_does_not_void_anothers(client) -> None:
    a, b = _person(client, "A").user, _person(client, "B").user
    code_a = _issue(client, a.id).claim_code
    _issue(client, b.id)
    assert _redeem(client, code_a).status_code == 200


def test_an_expired_code_is_gone(client) -> None:
    code = _issue(client, _person(client).user.id).claim_code
    _expire(code)
    assert _redeem(client, code).status_code == 410


def test_unknown_used_voided_and_expired_all_read_the_same(client) -> None:
    """So the route cannot be used to learn which codes exist."""
    person = _person(client).user
    used = _issue(client, person.id).claim_code
    _redeem(client, used)
    voided = _issue(client, person.id).claim_code
    _issue(client, person.id)
    expired = _issue(client, _person(client, "B").user.id).claim_code
    _expire(expired)
    unknown = "u" * 43

    replies = [_redeem(client, code) for code in (used, voided, expired, unknown)]

    assert {(r.status_code, r.text) for r in replies} == {(410, replies[0].text)}


@pytest.mark.parametrize("code", ["", "short", "has a space in it 12345", "x" * 200])
def test_a_code_of_the_wrong_shape_is_422_not_a_lookup(client, code) -> None:
    assert _redeem(client, code).status_code == 422


def test_issuing_is_audited_but_the_code_never_is(client) -> None:
    person = _person(client).user
    code = _issue(client, person.id).claim_code
    _redeem(client, code)

    listed = client.get("/admin/actions", headers=ADMIN).json()
    actions = AdminActionsOut.model_validate(listed).items

    assert [a.action.value for a in actions] == ["user.issue_claim", "user.create"]
    assert actions[0].target_id == person.id and actions[0].admin_id == "admin-1"
    assert code not in str(actions)
