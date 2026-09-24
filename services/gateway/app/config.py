from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: object) -> object:
    # pydantic-settings decodes list-typed env vars as JSON by default (e.g.
    # CORS_ORIGINS=["a","b"]), which is unfriendly to hand-edit in a .env
    # file. NoDecode below skips that JSON step so this validator can parse
    # the plain comma-separated form documented in .env.example instead.
    if isinstance(value, str):
        return [origin.strip() for origin in value.split(",") if origin.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "dev"
    PORT: int = 8000

    # DATABASE_URL and JWT_SECRET are required (no default) but not wired up
    # to anything yet — nothing connects to Postgres, and app/auth.py is
    # still the Week 2 stub. They're declared now, deliberately, so the
    # people adding real auth and the database next week have a single
    # place to read these values from, rather than each inventing their own
    # env-var name. Required-with-no-default is also what makes the
    # fail-loudly-at-startup behavior below possible in the first place.
    DATABASE_URL: str
    JWT_SECRET: str

    # Week 3 Phase 7: required-with-no-default, same fail-loud-at-startup
    # reasoning as DATABASE_URL/JWT_SECRET above — a one-time-generated
    # VAPID keypair (see README.md's "Configuration" section for the
    # `vapid --gen` process), never generated at runtime. VAPID_SUBJECT is
    # the `mailto:`/`https:` contact URL the VAPID spec requires in every
    # push request's JWT `sub` claim.
    VAPID_PRIVATE_KEY: str
    VAPID_PUBLIC_KEY: str
    VAPID_SUBJECT: str

    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: Annotated[list[str], NoDecode, BeforeValidator(_split_csv)] = Field(
        default_factory=list
    )

    # Week 4 Phase 8: how long a sender can undo a just-sent message before
    # app/undo.py's scheduled fan-out delivers it for real. See app/undo.py
    # for the in-memory scheduler this drives.
    UNDO_WINDOW_SECONDS: int = 30

    # Week 6: media storage (voice-note uploads, app/media.py). Required,
    # no default -- same fail-loud-at-startup reasoning as DATABASE_URL/
    # JWT_SECRET above, and deliberately never a hardcoded path here: the
    # real directory is an infrastructure choice (a mounted Docker volume
    # in docker-compose.yml, a plain local folder for bare development),
    # not something this file should silently pick on anyone's behalf. A
    # wrong value here doesn't corrupt anything (app/media_storage.py's
    # LocalDiskMediaStorage creates the directory on first use if it's
    # missing) but does mean every upload lands somewhere nobody chose on
    # purpose -- exactly the failure mode requiring this be set explicitly
    # avoids.
    MEDIA_STORAGE_ROOT: str
    # 2 MiB default: generous headroom over a 30-second Opus voice note's
    # real size (~100KB, see app/media_storage.py's module docstring for
    # the estimate and its basis) -- a 10-minute note would still fit --
    # while still bounding worst-case memory/disk use per upload.
    # Overridable per-deployment; unlike MEDIA_STORAGE_ROOT this has a
    # sane default and getting it wrong fails loudly per-request (413),
    # not at startup, so it doesn't need the same required-with-no-default
    # treatment.
    MEDIA_MAX_UPLOAD_BYTES: int = 2 * 1024 * 1024

    # Week 6: durable job queue (app/jobs.py, app/db/models.py's Job). All
    # have sane defaults, unlike MEDIA_STORAGE_ROOT above -- getting one of
    # these wrong doesn't corrupt data or point at nowhere, it just makes
    # the worker loop faster/slower or more/less patient, so there's no
    # fail-loud-at-startup reason to require them.
    JOB_POLL_INTERVAL_SECONDS: float = 1.0
    JOB_LEASE_SECONDS: int = 300
    JOB_MAX_ATTEMPTS: int = 5
    # Test-only escape hatch: tests/conftest.py's app fixture sets this to
    # False so the background worker loop never starts inside a FastAPI
    # TestClient's app -- tests that exercise the queue call
    # app/db/repository.py's functions directly instead. Real deployments
    # always leave this at the default True.
    JOB_WORKER_ENABLED: bool = True

    # Week 6: audio retention sweep (app/retention.py). 30 days is the
    # window mentioned informally so far (infra/backups/README.md's 30-day
    # BACKUP retention is a separate, unrelated setting for a different
    # thing -- this is the first place an actual audio-retention window is
    # enforced as code, not just a sentence in a plan doc). Configurable,
    # not hardcoded, since 30 is a starting guess, not a number anyone
    # signed off on -- see OPEN_QUESTIONS.md.
    MEDIA_RETENTION_DAYS: int = 30
    MEDIA_RETENTION_SWEEP_INTERVAL_SECONDS: float = 3600.0
    # Same test-only escape hatch as JOB_WORKER_ENABLED, same reason:
    # tests/conftest.py sets this False too, so a live sweep loop never
    # runs during a route test.
    MEDIA_RETENTION_SWEEP_ENABLED: bool = True

    # Week 6 Step 2: which AI service to call for anything AI-side (Week 6
    # only ever means services/ai/mock/ -- see app/jobs.py's
    # "transcribe_media" handler and its own module docstring for why real
    # ASR is explicitly Week 8, not now). Defaults to the hostname
    # docker-compose.yml's own `ai-services` service already uses, so this
    # points at the right place automatically once that service is the
    # real thing (or the mock) rather than today's Week-1 health-check
    # skeleton -- see OPEN_QUESTIONS.md for the gap that leaves open right
    # now.
    AI_SERVICE_URL: str = "http://ai-services:8001"


@lru_cache
def get_settings() -> Settings:
    # Constructing Settings() is what validates required env vars are
    # present. app/main.py calls this at import time (module level), not
    # lazily inside a route handler, so a missing DATABASE_URL or JWT_SECRET
    # crashes the process on startup with a readable pydantic error — not
    # silently, the first time some route happens to need it.
    return Settings()
