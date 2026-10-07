import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import get_db
from app.db.models import User as DbUser
from app.db.repository import get_or_create_user
from app.models import User
from app.tokens import InvalidToken, looks_like_jwt, verify_token

bearer = HTTPBearer(auto_error=False)


def user_from_token(token: str | None, db: Session) -> User:
    # STUB: replace body with real JWT verification (Week 2/3). Signature must
    # not change beyond the `db` param added below. This is the single place
    # that turns a raw token string into a User — both get_current_user (HTTP,
    # token from the Authorization header) and app/ws.py (WebSocket, token
    # from a ?token= query param, since browsers can't set custom headers on
    # a WS handshake) call through here rather than each having their own
    # copy of the verification logic.
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
    #
    # `db` param added: the widening above returns a UUID as `user.id` but
    # never persisted a matching `users` row for it, so any write with a FK
    # to `users.id` (circles.created_by first among them) 500ed with a
    # ForeignKeyViolation the first time a fresh UUID token actually got
    # used — reproduced live via a real join + POST /circles. get_or_create_user
    # provisions that row before returning, same "additive, not a behavior
    # change" reasoning as the widening it completes.
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Week 8: a token that LOOKS like a JWT is verified strictly in every mode -- a bad one is a
    # 401 and never falls through to the stub identity below. In `jwt` mode nothing else is
    # accepted at all. Only in the default `legacy` mode does a non-JWT token reach the stub.
    if looks_like_jwt(token):
        return _user_from_signed_token(token, db)
    if get_settings().AUTH_MODE == "jwt":
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        parsed = uuid.UUID(token)
    except ValueError:
        return User(id="stub-user-1", name="Test Elder", preferred_language="te", role="elder")
    # Retried once: this runs on every authenticated request (both HTTP and
    # WS), so it's the single hottest DB round-trip in the whole app --
    # exactly the one most likely to catch a transient stall on this
    # deployment's known-flaky network. app/db/base.py's statement_timeout
    # turns such a stall into a fast OperationalError instead of an
    # indefinite hang (good), but a stall that clears in a couple of
    # seconds is genuinely transient, not a real failure -- reproduced live
    # (2026-09-08): a provisioning insert got cancelled once, then the
    # identical call succeeded in 28ms moments later. A canceled statement
    # leaves the session's transaction unusable until rolled back, so that
    # has to happen before the retry can run any query at all.
    try:
        get_or_create_user(
            db, user_id=parsed, name="Test Elder", preferred_language="te", role="elder"
        )
    except OperationalError:
        db.rollback()
        get_or_create_user(
            db, user_id=parsed, name="Test Elder", preferred_language="te", role="elder"
        )
    return User(id=token, name="Test Elder", preferred_language="te", role="elder")


def _user_from_signed_token(token: str, db: Session) -> User:
    """A valid signature is not enough: the user must exist. A signed token for an id with no
    row is a 401 and creates nothing (the legacy stub provisioned rows; real auth must not).
    The role is the DATABASE's, so a demotion applies on the very next request."""
    try:
        user_id = verify_token(token)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    try:
        row = db.get(DbUser, user_id)
    except OperationalError:  # same one retry as the legacy path: this is the hottest query
        db.rollback()
        row = db.get(DbUser, user_id)
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return User(
        id=str(row.id), name=row.name, preferred_language=row.preferred_language, role=row.role
    )


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    return user_from_token(creds.credentials if creds is not None else None, db)


def require_role(*allowed_roles: str):
    async def _require_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _require_role
