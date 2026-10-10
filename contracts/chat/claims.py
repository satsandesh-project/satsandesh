"""
Signing in with a claim: `POST /onboarding/claim`.

An admin who has added a person (contracts/chat/admin_org.py) hands them a QR
holding a single-use claim code. The person's app exchanges it ONCE for a signed
session token; after that the QR is dead. This is the safe way to hand over an
account: the QR is not the credential, so a photographed or printed code is
worth nothing once used (or after it expires), where a session token in the QR
would be a 30-day, non-revocable password on paper.

No authentication on this route -- the code IS the authority -- so:

- a code that is unknown, already used or expired all answer the same **410**,
  with the same words, so the route cannot be used to find out which codes exist;
- a code of the wrong shape is a **422**, which tells an attacker nothing (the
  gateway issues only codes of the shape below);
- the response is a subset of `/onboarding/activate`'s (`ActivateResponse`), so an
  app that handles an invite can handle a claim with the same code.
"""

from __future__ import annotations

from pydantic import Field

from contracts.chat.common import VersionedModel
from contracts.chat.renderings import LANGUAGE_PATTERN

# What the gateway issues (URL-safe base64 of 32 random bytes is 43 characters).
# Bounded on both sides so an absurd value is refused before it is looked up.
CLAIM_CODE_PATTERN = r"^[A-Za-z0-9_-]{16,128}$"


class ClaimRedeemIn(VersionedModel):
    """Request body for `POST /onboarding/claim`."""

    claim_code: str = Field(pattern=CLAIM_CODE_PATTERN)


class ClaimRedeemedOut(VersionedModel):
    """Response for a successful `POST /onboarding/claim`. `access_token` is the
    person's signed session token (`Authorization: Bearer ...`); store it, and use
    it instead of any identity the browser made up before."""

    access_token: str
    user_id: str
    display_name: str
    language: str = Field(pattern=LANGUAGE_PATTERN)
