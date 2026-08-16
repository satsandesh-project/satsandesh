from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models import User

bearer = HTTPBearer(auto_error=False)


def user_from_token(token: str | None) -> User:
    # STUB: replace body with real JWT verification (Week 2). Signature must not change.
    # This is the single place that turns a raw token string into a User — both
    # get_current_user (HTTP, token from the Authorization header) and app/ws.py
    # (WebSocket, token from a ?token= query param, since browsers can't set
    # custom headers on a WS handshake) call through here rather than each
    # having their own copy of the verification logic.
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return User(id="stub-user-1", name="Test Elder", preferred_language="te", role="elder")


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
