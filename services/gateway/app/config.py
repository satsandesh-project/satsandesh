from functools import lru_cache
from typing import Annotated, Literal

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

    # Week 7: the real AI services are four separate processes on four ports
    # (speech 8002, moderation 8003, mt 8004, render 8005), while
    # services/ai/mock/ is one server answering all four paths. Each stage
    # therefore has its own optional URL, falling back to AI_SERVICE_URL when
    # unset -- so the mock (and today's single-host default) needs no change.
    AI_TRANSCRIBE_URL: str | None = None
    AI_PIVOT_URL: str | None = None
    AI_MODERATION_URL: str | None = None
    AI_RENDER_URL: str | None = None

    # Per-call timeouts (seconds). Deliberately longer than each service's own
    # limit: moderation gives up at 20s and answers 200 + HOLD (fail closed) --
    # a client that hung up first would turn that decision into a retry. CPU
    # ASR on a cold model is slow (the demo console allows 120s); render does
    # one MT + one TTS pass per target language.
    AI_TRANSCRIBE_TIMEOUT_S: float = 120.0
    AI_PIVOT_TIMEOUT_S: float = 90.0
    AI_MODERATION_TIMEOUT_S: float = 30.0
    AI_RENDER_TIMEOUT_S: float = 180.0

    # Where the ASR service sees the gateway's media store, as an absolute
    # path (the media_data volume mounted into the ASR container). The real
    # ASR resolves AudioRef.uri as a local path / file:// URI and does not
    # understand the gateway's `media:<id>` scheme, so with this unset it is
    # sent `media:<id>` (fine for the mock, which never opens the file).
    # PROVISIONAL: a shared volume is one of two ways to close that gap; the
    # other is a contract change that is M3's call (OPEN_QUESTIONS.md).
    AI_AUDIO_MOUNT_ROOT: str | None = None

    # Stopgap for the webm_opus gap: label a browser WebM/Opus note as
    # ogg_opus when calling the AI services. ffmpeg sniffs the container from
    # the bytes, so it decodes (checked against a real WebM/Opus file), but it
    # IS a mislabel in the request. Off by default; whether to add webm_opus
    # to contracts/ai or transcode here is M3's decision (OPEN_QUESTIONS.md #2).
    AI_ACCEPT_WEBM_AS_OGG_OPUS: bool = False

    # Week 8: how a bearer token becomes a user (app/auth.py, app/tokens.py).
    #   legacy  the default. A token that is a UUID is THAT user (the Week 3 stub: no signature,
    #           no expiry), so nothing existing breaks. A token that looks like a JWT is
    #           still verified strictly.
    #   jwt     ONLY a valid signed token is accepted; a bare UUID is a 401.
    # Turning `jwt` on for a deployment is step 2: the elder app mints its own random UUID in
    # the browser today and has no way to obtain a signed token yet (OPEN_QUESTIONS #32), so
    # switching it on before that would lock every elder out.
    AUTH_MODE: Literal["legacy", "jwt"] = "legacy"
    # How long a signed token lives. There is no refresh and no revocation yet, so this is also
    # how long a stolen one works. Elders are not asked to log in again often; 30 days.
    AUTH_TOKEN_TTL_SECONDS: int = 30 * 24 * 3600

    # Week 7 Phase 5: the pipeline orchestrator (app/pipeline.py,
    # ORCHESTRATOR_DESIGN.md). OFF by default: with it off nothing about
    # message creation or delivery changes, on staging or anywhere. Turning it
    # on needs the AI services reachable (services/ai/mock/ answers all four).
    PIPELINE_ENABLED: bool = False
    # What a classifier NUDGE does to delivery. The chat contract leaves the
    # action -> status mapping open; this assumes a nudge records a notice and
    # still delivers. Set false to hold nudged messages for a human instead.
    PIPELINE_NUDGE_DELIVERS: bool = True
    # Where the GATEWAY sees the render service's output directory (an
    # absolute path). The render service reports a file:// path on ITS disk;
    # the gateway reads the file by name from here, so the two containers may
    # mount the directory at different paths. Unset: rendering audio cannot
    # be ingested and every rendering is text-only (tts_skipped).
    AI_RENDER_AUDIO_ROOT: str | None = None

    # Whether POST /media enqueues a "transcribe_media" job for each new
    # upload (app/jobs.py). Off by default: the job only calls the AI
    # service and LOGS the result -- nothing stores a transcript until Week
    # 7's orchestrator exists -- and on a deployment whose ai-services is
    # still a health-check stub (staging today) every such job would just
    # fail. Real browser voice notes are also webm_opus, which
    # contracts/ai's AudioFormat has no value for (OPEN_QUESTIONS.md #2),
    # so those jobs are rejected as permanent failures anyway. Turn on to
    # exercise the queue end to end against services/ai/mock/.
    TRANSCRIBE_ON_UPLOAD_ENABLED: bool = False

    # Re-schedule delivery of every still-`pending` message at startup
    # (app/recovery.py), so a gateway restart inside or after a message's
    # undo window doesn't strand it forever -- app/undo.py's registry is
    # in-memory. Same test-only off switch as JOB_WORKER_ENABLED:
    # tests/conftest.py disables it so a TestClient's lifespan doesn't scan
    # the test database.
    STARTUP_RECOVERY_ENABLED: bool = True


@lru_cache
def get_settings() -> Settings:
    # Constructing Settings() is what validates required env vars are
    # present. app/main.py calls this at import time (module level), not
    # lazily inside a route handler, so a missing DATABASE_URL or JWT_SECRET
    # crashes the process on startup with a readable pydantic error — not
    # silently, the first time some route happens to need it.
    return Settings()
