from fastapi import Depends, FastAPI, Response

from app.auth import get_current_user, require_role
from app.models import User

app = FastAPI(
    title="SatSandesh Gateway",
    version="0.1.0",
    description=(
        "Single front door for SatSandesh clients. Week 1 skeleton only — no "
        "routes, no proxying to services/ai/ or the chat backbone yet."
    ),
)


@app.get("/health")
def health() -> dict:
    # Liveness probe. Docker Compose's healthcheck:, Caddy's upstream check,
    # and Uptime-Kuma all hit this directly and restart/evict the process on
    # failure. It must stay dependency-free and instant: if it touched the
    # database or services/ai/, a brief blip in either would get an
    # otherwise-healthy gateway killed for no reason. That's what
    # /health/ready is for — keep the two split.
    return {"status": "ok"}


@app.get("/health/ready")
def ready(response: Response) -> dict:
    # Readiness probe: safe to check real dependencies here, unlike /health.
    # Once a dependency check below reports unhealthy, this must return
    # HTTP 503 (not 200) so upstreams stop routing traffic to this instance —
    # /health must keep returning 200 regardless.
    checks: dict = {}

    # TODO(readiness-checks): plug in real dependency checks here as they
    # land, each writing its own entry into `checks`, e.g.:
    #   checks["postgres"] = await _check_postgres()
    #   checks["ai_service"] = await _check_ai_service()
    # If any entry above is unhealthy, set response.status_code = 503 and
    # status to "degraded" — don't leak versions or dependency internals
    # (hostnames, DSNs, stack traces) to this unauthenticated endpoint.

    return {"status": "ok", "checks": checks}


@app.get("/me")
def me(user: User = Depends(get_current_user)) -> User:
    return user


@app.get("/moderator-only")
def moderator_only(user: User = Depends(require_role("moderator"))) -> User:
    # Demo route only, to exercise the 403 (wrong-role) path. Not a real feature.
    return user
