"""
`POST /onboarding/claim` in the mock chat gateway, and the claim store that
`POST /admin/users/{id}/claim` (mock/admin_org.py) issues into.

Same rules as the real gateway, so an app written against this works there:
a code works once; an unknown, used, voided or expired code all answer the same
410 in the same words; issuing a new claim for a person voids their unredeemed
earlier one. In-memory, resets on restart.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from contracts.chat.claims import ClaimRedeemedOut, ClaimRedeemIn

router = APIRouter(tags=["onboarding"])

CLAIM_TTL = timedelta(days=7)
GONE = "This code has already been used, has expired, or is not valid."


@dataclass
class _Claim:
    user_id: str
    expires_at: datetime
    used: bool = False
    voided: bool = False


_claims: dict[str, _Claim] = {}


def reset() -> None:
    _claims.clear()


def issue(user_id: str) -> tuple[str, datetime]:
    """A fresh code for `user_id`; their earlier unredeemed codes stop working."""
    for claim in _claims.values():
        if claim.user_id == user_id and not claim.used:
            claim.voided = True
    code = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + CLAIM_TTL
    _claims[code] = _Claim(user_id=user_id, expires_at=expires_at)
    return code, expires_at


@router.post("/onboarding/claim", response_model=ClaimRedeemedOut)
def redeem_claim(body: ClaimRedeemIn) -> ClaimRedeemedOut:
    from contracts.chat.mock import admin_org as mock_admin

    claim = _claims.get(body.claim_code)
    now = datetime.now(timezone.utc)
    if claim is None or claim.used or claim.voided or claim.expires_at <= now:
        raise HTTPException(status_code=410, detail=GONE)
    claim.used = True
    user = mock_admin._users[claim.user_id]
    return ClaimRedeemedOut(
        access_token=mock_admin._token_for(user.id),
        user_id=user.id,
        display_name=user.name,
        language=user.preferred_language,
    )
