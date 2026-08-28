import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models import User

bearer = HTTPBearer(auto_error=False)


def user_from_token(token: str | None) -> User:
    # STUB: replace body with real JWT verification (Week 2/3). Signature must
    # not change. This is the single place that turns a raw token string into
    # a User — both get_current_user (HTTP, token from the Authorization
    # header) and app/ws.py (WebSocket, token from a ?token= query param,
    # since browsers can't set custom headers on a WS handshake) call through
    # here rather than each having their own copy of the verification logic.
    #
    # Week 3 Phase 7 widening: still zero real verification (no signature, no
    # expiry — still very much a stub), but a token that happens to parse as
    # a UUID is now taken as *that* user's real id, instead of always
    # collapsing to the same hardcoded identity. Every DB-touching route does
    # uuid.UUID(user.id) to address the `users` table, so the old hardcoded
    # "stub-user-1" (not a UUID) 500ed on any of them the moment a real
    # server — not a test with dependency_overrides — actually ran one; and a
    # single fixed identity regardless of token made it structurally
    # impossible to authenticate as two different users for manual
    # multi-party verification (a push notification test needs a sender
    # distinct from the recipient). Any token that isn't a valid UUID falls
    # back to the exact old hardcoded stub, unchanged, so this is additive,
    # not a behavior change for existing callers.
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        uuid.UUID(token)
    except ValueError:
        return User(id="stub-user-1", name="Test Elder", preferred_language="te", role="elder")
    return User(id=token, name="Test Elder", preferred_language="te", role="elder")


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    return user_from_token(creds.credentials if creds is not None else None)


def require_role(*allowed_roles: str):
    async def _require_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _require_role
