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


@lru_cache
def get_settings() -> Settings:
    # Constructing Settings() is what validates required env vars are
    # present. app/main.py calls this at import time (module level), not
    # lazily inside a route handler, so a missing DATABASE_URL or JWT_SECRET
    # crashes the process on startup with a readable pydantic error — not
    # silently, the first time some route happens to need it.
    return Settings()
