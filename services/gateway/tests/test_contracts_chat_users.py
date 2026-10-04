"""Week 7: the per-user settings contract (`GET/PATCH /me/settings`).

M1's elder app has called this endpoint since Week 5 and fails soft because the
gateway never had it. The shapes here are what that client already sends and
reads: `quiet_hours_start/end` as "HH:MM:SS" (null clears), `preferred_language`
(en/hi/te) and `tts_on`, all in one PATCH.

The one subtle rule: PATCH must tell "field omitted" (leave it alone) from
"field set to null" (clear it) -- the client relies on an explicit null to clear a
quiet-hours time the elder emptied. Pydantic can: `model_fields_set`.
"""

from datetime import time

import pytest
from contracts.chat.common import CONTRACTS_VERSION
from contracts.chat.users import UserSettingsOut, UserSettingsUpdate
from pydantic import ValidationError


def test_the_exact_body_the_elder_app_sends_parses() -> None:
    body = {
        "quiet_hours_start": "22:00:00",
        "quiet_hours_end": "07:00:00",
        "preferred_language": "hi",
        "tts_on": False,
    }
    update = UserSettingsUpdate.model_validate(body)
    assert update.quiet_hours_start == time(22, 0)
    assert update.quiet_hours_end == time(7, 0)
    assert update.preferred_language == "hi" and update.tts_on is False


def test_an_empty_patch_changes_nothing() -> None:
    assert UserSettingsUpdate.model_validate({}).model_fields_set == set()


def test_omitted_and_explicit_null_are_distinguishable() -> None:
    omitted = UserSettingsUpdate.model_validate({"tts_on": True})
    cleared = UserSettingsUpdate.model_validate({"tts_on": True, "quiet_hours_start": None})
    assert "quiet_hours_start" not in omitted.model_fields_set
    assert "quiet_hours_start" in cleared.model_fields_set
    assert cleared.quiet_hours_start is None


@pytest.mark.parametrize("field", ["preferred_language", "tts_on"])
def test_a_field_that_cannot_be_empty_cannot_be_nulled(field) -> None:
    # Both are NOT NULL in the database; an explicit null is a client bug, not a request.
    with pytest.raises(ValidationError, match=field):
        UserSettingsUpdate.model_validate({field: None})


@pytest.mark.parametrize("language", ["", "h", "HI", "hi-IN", "Hindi"])
def test_the_language_is_a_bare_primary_subtag(language) -> None:
    with pytest.raises(ValidationError):
        UserSettingsUpdate.model_validate({"preferred_language": language})


@pytest.mark.parametrize("value", ["yes", "false", 1, 0, "true"])
def test_tts_on_must_be_a_real_boolean(value) -> None:
    # The client sends JSON true/false; a stringly-typed "false" silently becoming
    # False (or "yes" becoming True) would hide a client bug.
    with pytest.raises(ValidationError):
        UserSettingsUpdate.model_validate({"tts_on": value})


@pytest.mark.parametrize("value", ["25:00:00", "7pm", "", "22:00:61"])
def test_an_invalid_time_is_refused(value) -> None:
    with pytest.raises(ValidationError):
        UserSettingsUpdate.model_validate({"quiet_hours_start": value})


def test_hh_mm_without_seconds_is_accepted_too() -> None:
    assert UserSettingsUpdate.model_validate({"quiet_hours_end": "07:30"}).quiet_hours_end == time(
        7, 30
    )


def test_timezone_may_be_cleared_but_not_empty() -> None:
    assert UserSettingsUpdate.model_validate({"timezone": None}).timezone is None
    with pytest.raises(ValidationError):
        UserSettingsUpdate.model_validate({"timezone": ""})


def test_the_response_serialises_times_the_way_the_client_reads_them() -> None:
    out = UserSettingsOut(
        preferred_language="te",
        tts_on=True,
        quiet_hours_start=time(20, 0),
        quiet_hours_end=time(7, 0),
        timezone="Asia/Kolkata",
    )
    dumped = out.model_dump(mode="json")
    assert dumped["quiet_hours_start"] == "20:00:00"  # the client slices [:5] for <input type=time>
    assert dumped["quiet_hours_end"] == "07:00:00"
    assert dumped["contract_version"] == CONTRACTS_VERSION == "0.6.0"


def test_the_response_has_no_quiet_hours_or_timezone_until_set() -> None:
    out = UserSettingsOut(preferred_language="te", tts_on=True)
    assert (out.quiet_hours_start, out.quiet_hours_end, out.timezone) == (None, None, None)
