import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_required_vars_fail_loudly(monkeypatch):
    # DATABASE_URL and JWT_SECRET are required, with no default. Constructing
    # Settings without them must raise immediately, with both missing field
    # names readable in the error — not construct successfully with a
    # None/empty value that only blows up later when some route finally
    # reaches for settings.database_url at 2am. `_env_file=None` disables
    # .env loading for just this instance, so this test proves the failure
    # even though a real developer .env with valid values sits right next to
    # it in this repo.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    message = str(exc_info.value)
    assert "DATABASE_URL" in message
    assert "JWT_SECRET" in message


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.DATABASE_URL == "postgresql://user:pass@localhost:5432/db"
    assert settings.JWT_SECRET == "test-secret"
    assert settings.APP_ENV == "dev"
    assert settings.PORT == 8000
    assert settings.LOG_LEVEL == "INFO"
    assert settings.CORS_ORIGINS == []
