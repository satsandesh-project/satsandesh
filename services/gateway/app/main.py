import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.audio_labels import router as audio_labels_router
from app.auth import get_current_user
from app.circles import router as circles_router
from app.config import get_settings
from app.db.base import check_database_connection
from app.jobs import run_worker_loop, worker_id
from app.media import router as media_router
from app.messages import router as messages_router
from app.models import User
from app.onboarding import router as onboarding_router
from app.push import router as push_router
from app.retention import run_retention_sweep_loop
from app.ws import router as ws_router

# Called at import time, not inside a request handler: a missing DATABASE_URL
# or JWT_SECRET must crash the process on startup with a readable error, not
# wait silently until the first request that happens to need it.
settings = get_settings()

logging.basicConfig(level=settings.LOG_LEVEL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Week 6: the job-queue worker (app/jobs.py) lives inside this same
    # process, started here rather than as a separate service -- see
    # app/jobs.py's module docstring for why. JOB_WORKER_ENABLED defaults
    # to True for a real deployment; tests/conftest.py sets it False so
    # route tests (which trigger this same lifespan via TestClient's
    # context manager) don't get a live worker racing their db_session.
    # Week 6 Step 1: the retention sweeper (app/retention.py) runs the
    # same way, on its own timer, for the same reason -- a background loop
    # inside this process, not a separate cron job, so it survives however
    # this service is actually deployed without needing a second thing to
    # configure. MEDIA_RETENTION_SWEEP_ENABLED gets the same test-disable
    # treatment as JOB_WORKER_ENABLED, same reason.
    stop_event = asyncio.Event()
    worker_task: asyncio.Task | None = None
    retention_task: asyncio.Task | None = None
    if settings.JOB_WORKER_ENABLED:
        worker_task = asyncio.create_task(run_worker_loop(stop_event, this_worker_id=worker_id()))
    if settings.MEDIA_RETENTION_SWEEP_ENABLED:
        retention_task = asyncio.create_task(run_retention_sweep_loop(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        if worker_task is not None:
            await worker_task
        if retention_task is not None:
            await retention_task


app = FastAPI(
    title="SatSandesh Gateway",
    version="0.1.0",
    description=(
        "Single front door for SatSandesh clients. Week 1 skeleton only — no "
        "routes, no proxying to services/ai/ or the chat backbone yet."
    ),
    lifespan=lifespan,
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
app.include_router(onboarding_router)
app.include_router(push_router)
app.include_router(audio_labels_router)
app.include_router(media_router)


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
