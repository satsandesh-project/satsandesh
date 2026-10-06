"""Signed session tokens: what `app/auth.py` verifies once `AUTH_MODE=jwt`.

HS256 JWTs signed with `JWT_SECRET`. Claims: `sub` (the user's UUID), `iss`
("satsandesh-gateway"), `iat`, `exp`; all four are REQUIRED on verification. The role is
deliberately NOT a claim: it is read from `users.role` on every request, so demoting a
moderator takes effect at once instead of when their token expires.

What verification refuses, and why each is a test: an unsigned token (`alg: none`), a token
signed with a different algorithm than the one pinned here (algorithm confusion), a wrong key,
an expired token, a missing or non-UUID subject, a wrong issuer, and any payload edited after
signing. Anything at all that goes wrong becomes `InvalidToken`, which the caller turns into a
401: a malformed token must never be a 500.

Who may MINT one: the gateway itself (onboarding's `/activate`) and an operator with the
secret (`python -m app.tokens <user-uuid>`, used by the proof scripts and to give a moderator
a token). There is no self-registration endpoint: how the elder app gets its first token is
OPEN_QUESTIONS #32.

Not here, on purpose: revocation (a stolen token is good until it expires), refresh, and
rotating the secret. `JWT_SECRET` also signs onboarding invites; the two formats cannot be
confused (an invite is `id.expires.hex`, not a JWT) but they share a key, so a weak secret
weakens both.
"""

from __future__ import annotations

import argparse
import uuid
from datetime import UTC, datetime, timedelta

from jose import jwt

from app.config import get_settings

ISSUER = "satsandesh-gateway"
ALGORITHM = "HS256"
_REQUIRED = {"require_exp": True, "require_sub": True, "require_iss": True, "require_iat": True}


class InvalidToken(Exception):
    """The token is not a valid, current, correctly signed session token."""


def looks_like_jwt(token: str) -> bool:
    """Three dot-separated parts. Legacy tokens (a UUID, or a test string) never contain a dot,
    so a token that looks like a JWT is ALWAYS verified strictly, in every mode."""
    return token.count(".") == 2


def issue_token(
    user_id: uuid.UUID | str, *, ttl_seconds: int | None = None, now: datetime | None = None
) -> str:
    settings = get_settings()
    issued = now or datetime.now(UTC)
    ttl = settings.AUTH_TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    claims = {
        "sub": str(user_id),
        "iss": ISSUER,
        "iat": int(issued.timestamp()),
        "exp": int((issued + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(claims, settings.JWT_SECRET, algorithm=ALGORITHM)


def verify_token(token: str) -> uuid.UUID:
    """The user id the token was issued for, or InvalidToken."""
    try:
        claims = jwt.decode(
            token,
            get_settings().JWT_SECRET,
            algorithms=[ALGORITHM],  # pinned: never trust the token's own `alg`
            issuer=ISSUER,
            options=_REQUIRED,
        )
        return uuid.UUID(claims["sub"])
    except Exception as exc:  # noqa: BLE001 -- anything wrong with a token is "invalid", never a 500
        raise InvalidToken(type(exc).__name__) from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Print a signed session token for a user id.")
    parser.add_argument("user_id", type=uuid.UUID)
    parser.add_argument("--ttl", type=int, default=None, help="lifetime in seconds")
    args = parser.parse_args(argv)
    print(issue_token(args.user_id, ttl_seconds=args.ttl))


if __name__ == "__main__":
    main()
