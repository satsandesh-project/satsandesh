"""Session tokens: a client gets one on connect, the gateway verifies it
on every message.

Not the final auth system -- there's no password, no identity provider,
still no way to stop someone claiming "bob" if "bob" hasn't claimed it
first. What this DOES fix, per this week's task: a message's sender_id
is no longer a bare string the client asserts fresh on every single
message. A client proves identity once (POST /session, currently just by
picking a display name -- real identity verification is later work, not
this week's), gets a signed token binding that identity, and the gateway
verifies the signature + expiry on every WS message after that. Nobody
can forge a message as "bob" without either being issued bob's token or
breaking HMAC-SHA256 with a secret they don't have.

Token shape: base64url(user_id:expiry_unix_ts) "." hex(hmac_sha256).
Deliberately not JWT -- a full JWT library is more machinery than a
two-field signed token needs here, and stdlib hmac/hashlib is one less
dependency to reason about for something this narrow.
"""

import base64
import hashlib
import hmac
import os
import re
import time

SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev_only_insecure_session_secret_change_me")
TOKEN_TTL_SECONDS = int(os.environ.get("SESSION_TOKEN_TTL_SECONDS", str(24 * 60 * 60)))

_USER_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")


class AuthError(Exception):
    """Malformed token, bad signature, or expired -- callers turn this
    into a 401/close code, not a 500."""


def _sanitize_user_id(display_name: str) -> str:
    """Same lowercase/underscore convention circles' Matrix backend
    already relies on (see matrix_circle_store.py's _localpart) --
    reusing it here means a token's user_id is always safe to pass
    straight through to any backbone without a second sanitization step."""
    candidate = display_name.strip().lower()
    candidate = re.sub(r"[^a-z0-9_]", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_")
    if not candidate:
        raise AuthError("display name has no usable characters after sanitizing")
    return candidate[:32]


def issue_token(display_name: str, ttl_seconds: int = TOKEN_TTL_SECONDS) -> tuple:
    """Returns (token, user_id). user_id is the sanitized form actually
    bound into the token -- callers should show the client what it is,
    since it may differ from what they typed."""
    user_id = _sanitize_user_id(display_name)
    expiry = int(time.time()) + ttl_seconds
    payload = f"{user_id}:{expiry}"
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii") + "." + signature
    return token, user_id


def verify_token(token: str) -> str:
    """Returns the verified user_id, or raises AuthError."""
    try:
        encoded_payload, signature = token.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8")
        user_id, expiry_str = payload.rsplit(":", 1)
        expiry = int(expiry_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError(f"malformed token: {exc}") from exc

    expected_signature = hmac.new(
        SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    # constant-time compare -- a timing side-channel on token verification
    # would be a real, if minor, vulnerability to leave in even a "not the
    # final auth system" stage.
    if not hmac.compare_digest(signature, expected_signature):
        raise AuthError("bad signature")

    if time.time() > expiry:
        raise AuthError("token expired")

    if not _USER_ID_RE.match(user_id):
        # Can only happen if SESSION_SECRET changed and a stale token
        # happens to still verify against garbage -- defense in depth,
        # not an expected path.
        raise AuthError(f"token names an invalid user_id: {user_id!r}")

    return user_id
