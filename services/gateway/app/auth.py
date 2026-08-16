from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.models import User

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> User:
    # STUB: replace body with real JWT verification (Week 2). Signature must not change.
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return User(id="stub-user-1", name="Test Elder", preferred_language="te", role="elder")


def require_role(*allowed_roles: str):
    async def _require_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _require_role
