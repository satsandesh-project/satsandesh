"""contracts/chat/claims.py and `AdminClaimOut`: the single-use sign-in claim.

Golden fixtures (one canonical example each; they fail if a field drifts without
a version bump) and the rules the models enforce on their own.
"""

import json
import re
import secrets
from pathlib import Path

import pytest
from contracts.chat.admin_org import AdminActionKind, AdminClaimOut
from contracts.chat.claims import CLAIM_CODE_PATTERN, ClaimRedeemedOut, ClaimRedeemIn
from contracts.chat.common import CONTRACTS_VERSION
from pydantic import BaseModel, ValidationError

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "chat"

MODELS_BY_FIXTURE: dict[str, type[BaseModel]] = {
    "admin_claim_out.json": AdminClaimOut,
    "claim_redeem_in.json": ClaimRedeemIn,
    "claim_redeemed_out.json": ClaimRedeemedOut,
}

A_CODE = "q7Xk2mVn9RtYb4LwPzC8sHdJfG3aEu5oIxN1vBcT6yA"


@pytest.mark.parametrize("filename,model_cls", MODELS_BY_FIXTURE.items())
def test_golden_fixture_parses_and_round_trips(filename, model_cls) -> None:
    raw = json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    assert model_cls.model_validate(raw).model_dump(mode="json") == raw
    assert raw["contract_version"] == CONTRACTS_VERSION


def test_issuing_a_claim_is_a_named_audit_action() -> None:
    assert AdminActionKind("user.issue_claim") is AdminActionKind.USER_ISSUE_CLAIM


@pytest.mark.parametrize(
    "code",
    [
        "",
        "short",
        "x" * 15,
        "x" * 129,
        "has a space in it 1234",
        "slash/in/it/1234567890",
        "é" * 20,
    ],
)
def test_a_code_of_the_wrong_shape_is_refused_before_any_lookup(code) -> None:
    with pytest.raises(ValidationError):
        ClaimRedeemIn.model_validate({"claim_code": code})


@pytest.mark.parametrize("code", [A_CODE, "x" * 16, "A_b-9" * 25])
def test_what_the_gateway_issues_is_accepted(code) -> None:
    assert ClaimRedeemIn.model_validate({"claim_code": code}).claim_code == code


def test_the_pattern_matches_the_43_characters_of_32_random_bytes() -> None:
    for _ in range(50):
        assert re.fullmatch(CLAIM_CODE_PATTERN, secrets.token_urlsafe(32))


def test_the_language_is_a_bare_primary_subtag() -> None:
    base = {"access_token": "t", "user_id": "u", "display_name": "n"}
    ClaimRedeemedOut.model_validate({**base, "language": "te"})
    for bad in ("", "Telugu", "te-IN", "TE"):
        with pytest.raises(ValidationError):
            ClaimRedeemedOut.model_validate({**base, "language": bad})
