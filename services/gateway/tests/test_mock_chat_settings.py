"""`GET/PATCH /me/settings` in the mock chat gateway: what M1 builds the settings
card against, with the same omitted-vs-null semantics as the real route."""

import uuid

import pytest
from contracts.chat.mock.app import app
from contracts.chat.users import UserSettingsOut
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _as(user):
    return {"X-Mock-User-Id": user}


def test_a_new_user_has_the_defaults(client) -> None:
    out = UserSettingsOut.model_validate(
        client.get("/me/settings", headers=_as(str(uuid.uuid4()))).json()
    )
    assert out.tts_on is True and out.quiet_hours_start is None and out.timezone is None


def test_patch_with_the_elder_apps_body_is_stored_and_read_back(client) -> None:
    who = _as(str(uuid.uuid4()))
    resp = client.patch(
        "/me/settings",
        headers=who,
        json={
            "quiet_hours_start": "22:00:00",
            "quiet_hours_end": "07:00:00",
            "preferred_language": "hi",
            "tts_on": False,
        },
    )
    assert resp.status_code == 200
    again = UserSettingsOut.model_validate(client.get("/me/settings", headers=who).json())
    assert (again.preferred_language, again.tts_on) == ("hi", False)
    assert again.quiet_hours_start.hour == 22


def test_omitted_fields_are_left_alone_and_null_clears(client) -> None:
    who = _as(str(uuid.uuid4()))
    client.patch(
        "/me/settings", headers=who, json={"quiet_hours_start": "22:00:00", "tts_on": False}
    )
    client.patch("/me/settings", headers=who, json={"preferred_language": "te"})  # nothing else
    mid = UserSettingsOut.model_validate(client.get("/me/settings", headers=who).json())
    assert mid.tts_on is False and mid.quiet_hours_start is not None

    client.patch("/me/settings", headers=who, json={"quiet_hours_start": None})
    end = UserSettingsOut.model_validate(client.get("/me/settings", headers=who).json())
    assert end.quiet_hours_start is None and end.tts_on is False


def test_settings_are_per_user(client) -> None:
    a, b = _as(str(uuid.uuid4())), _as(str(uuid.uuid4()))
    client.patch("/me/settings", headers=a, json={"tts_on": False})
    assert (
        UserSettingsOut.model_validate(client.get("/me/settings", headers=b).json()).tts_on is True
    )


def test_an_invalid_patch_is_422(client) -> None:
    assert client.patch("/me/settings", headers=_as("u"), json={"tts_on": None}).status_code == 422
