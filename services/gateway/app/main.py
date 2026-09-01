import logging

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.audio_labels import router as audio_labels_router
from app.auth import get_current_user
from app.circles import router as circles_router
from app.config import get_settings
from app.db.base import check_database_connection
from app.messages import router as messages_router
from app.models import User
from app.push import router as push_router
from app.ws import router as ws_router

# Called at import time, not inside a request handler: a missing DATABASE_URL
# or JWT_SECRET must crash the process on startup with a readable error, not
# wait silently until the first request that happens to need it.
settings = get_settings()

logging.basicConfig(level=settings.LOG_LEVEL)

app = FastAPI(
    title="SatSandesh Gateway",
    version="0.1.0",
    description=(
        "Single front door for SatSandesh clients. Week 1 skeleton only — no "
        "routes, no proxying to services/ai/ or the chat backbone yet."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_router)
app.include_router(messages_router)
app.include_router(circles_router)
app.include_router(push_router)
app.include_router(audio_labels_router)


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
    checks: dict = {"postgres": "ok" if check_database_connection() else "unreachable"}

    # TODO(readiness-checks): plug in further dependency checks here as they
    # land, each writing its own entry into `checks`, e.g.:
    #   checks["ai_service"] = await _check_ai_service()
    # Don't leak versions or dependency internals (hostnames, DSNs, stack
    # traces) to this unauthenticated endpoint — "ok"/"unreachable" only.

    if any(value != "ok" for value in checks.values()):
        response.status_code = 503
        return {"status": "degraded", "checks": checks}

    return {"status": "ok", "checks": checks}


@app.get("/me")
def me(user: User = Depends(get_current_user)) -> User:
    return user
