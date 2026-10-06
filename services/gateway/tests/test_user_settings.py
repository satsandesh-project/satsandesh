"""`GET/PATCH /me/settings` on the real gateway (Week 7).

The elder app has called this since Week 5 and failed soft because it did not
exist. It is also the prerequisite for turning the pipeline on: the
orchestrator renders only into the languages the RECIPIENTS prefer
(`users.preferred_language`), and until a user could change it, that was Telugu
for everyone. And quiet hours, which push (`is_quiet_hours`) has read since
Month 1, had no way to be set.

Written before app/users.py exists.
"""

import uuid
from datetime import UTC, datetime, time

import pytest

from app.db.models import User as DbUser
from app.push import is_quiet_hours


def _bearer(token) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _new_token() -> str:
    return str(uuid.uuid4())


def _row(db_session, token) -> DbUser | None:
    db_session.rollback()  # see only what was actually COMMITTED
    db_session.expire_all()
    return db_session.get(DbUser, uuid.UUID(token))


# --- GET ------------------------------------------------------------------------------


def test_a_request_without_a_token_is_401(client):
    assert client.get("/me/settings").status_code == 401
    assert client.patch("/me/settings", json={}).status_code == 401


def test_a_brand_new_user_gets_the_defaults(client):
    token = _new_token()

    resp = client.get("/me/settings", headers=_bearer(token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_version"] == "0.6.0"
    assert body["preferred_language"] == "te"  # what the stub auth provisions today
    assert body["tts_on"] is True
    assert body["quiet_hours_start"] is None and body["quiet_hours_end"] is None
    assert body["timezone"] is None


def test_a_token_that_is_not_a_user_id_has_no_settings(client):
    # The stub's fixed fallback identity has no users row to hold any.
    assert client.get("/me/settings", headers=_bearer("not-a-uuid")).status_code == 404


def test_stored_settings_are_read_back_in_the_wire_format_the_client_expects(client, db_session):
    token = _new_token()
    db_session.add(
        DbUser(
            id=uuid.UUID(token),
            name="E",
            preferred_language="hi",
            tts_on=False,
            quiet_hours_start=time(21, 30),
            quiet_hours_end=time(6, 0),
            timezone="Asia/Kolkata",
        )
    )
    db_session.commit()

    body = client.get("/me/settings", headers=_bearer(token)).json()

    assert body["preferred_language"] == "hi" and body["tts_on"] is False
    assert body["quiet_hours_start"] == "21:30:00"  # the client slices [:5] for <input type=time>
    assert body["quiet_hours_end"] == "06:00:00"
    assert body["timezone"] == "Asia/Kolkata"


# --- PATCH ----------------------------------------------------------------------------------


def test_the_exact_body_the_elder_app_sends_is_accepted_and_returned(client):
    token = _new_token()

    resp = client.patch(
        "/me/settings",
        headers=_bearer(token),
        json={
            "quiet_hours_start": "22:00:00",
            "quiet_hours_end": "07:00:00",
            "preferred_language": "hi",
            "tts_on": False,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert (body["preferred_language"], body["tts_on"]) == ("hi", False)
    assert (body["quiet_hours_start"], body["quiet_hours_end"]) == ("22:00:00", "07:00:00")


def test_a_first_time_users_patch_is_actually_committed(client, db_session):
    """The auth stub only FLUSHES a new user's row, so a request that does not
    commit loses it (that is why a first GET persists nothing). PATCH is the one
    that must: after the session is rolled back, the row and its settings are
    still there."""
    token = _new_token()

    client.patch("/me/settings", headers=_bearer(token), json={"preferred_language": "hi"})

    row = _row(db_session, token)
    assert row is not None
    assert row.preferred_language == "hi"


def test_the_change_is_visible_to_the_next_get(client):
    token = _new_token()
    client.patch("/me/settings", headers=_bearer(token), json={"tts_on": False})

    assert client.get("/me/settings", headers=_bearer(token)).json()["tts_on"] is False


def test_omitted_fields_are_left_alone(client):
    token = _new_token()
    client.patch(
        "/me/settings",
        headers=_bearer(token),
        json={"preferred_language": "hi", "tts_on": False, "quiet_hours_start": "22:00:00"},
    )

    client.patch("/me/settings", headers=_bearer(token), json={"tts_on": True})

    body = client.get("/me/settings", headers=_bearer(token)).json()
    assert body["tts_on"] is True
    assert body["preferred_language"] == "hi"
    assert body["quiet_hours_start"] == "22:00:00"


def test_an_explicit_null_clears_quiet_hours_the_way_an_emptied_input_does(client):
    token = _new_token()
    client.patch(
        "/me/settings",
        headers=_bearer(token),
        json={"quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00"},
    )

    client.patch(
        "/me/settings",
        headers=_bearer(token),
        json={"quiet_hours_start": None, "quiet_hours_end": "06:00:00"},
    )

    body = client.get("/me/settings", headers=_bearer(token)).json()
    assert body["quiet_hours_start"] is None
    assert body["quiet_hours_end"] == "06:00:00"


def test_an_empty_patch_is_a_no_op(client):
    token = _new_token()
    client.patch("/me/settings", headers=_bearer(token), json={"tts_on": False})

    resp = client.patch("/me/settings", headers=_bearer(token), json={})

    assert resp.status_code == 200 and resp.json()["tts_on"] is False


@pytest.mark.parametrize("language", ["en", "hi", "te"])
def test_every_supported_language_is_accepted(client, language):
    resp = client.patch(
        "/me/settings", headers=_bearer(_new_token()), json={"preferred_language": language}
    )
    assert resp.status_code == 200 and resp.json()["preferred_language"] == language


@pytest.mark.parametrize("language", ["ta", "kn", "fr", "zz"])
def test_a_language_the_pipeline_cannot_render_is_refused(client, db_session, language):
    """Well-formed but unsupported. Storing it would make the orchestrator skip
    this user's renderings with no error anywhere."""
    token = _new_token()

    resp = client.patch(
        "/me/settings", headers=_bearer(token), json={"preferred_language": language}
    )

    assert resp.status_code == 422
    assert _row(db_session, token) is None or _row(db_session, token).preferred_language == "te"


@pytest.mark.parametrize(
    "body",
    [
        {"preferred_language": "hi-IN"},
        {"preferred_language": "HI"},
        {"preferred_language": ""},
        {"preferred_language": None},
        {"tts_on": None},
        {"tts_on": "false"},
        {"quiet_hours_start": "25:00:00"},
        {"quiet_hours_end": "soon"},
    ],
)
def test_a_malformed_patch_is_a_422_and_changes_nothing(client, db_session, body):
    token = _new_token()
    client.patch("/me/settings", headers=_bearer(token), json={"tts_on": False})

    resp = client.patch("/me/settings", headers=_bearer(token), json=body)

    assert resp.status_code == 422
    assert client.get("/me/settings", headers=_bearer(token)).json()["tts_on"] is False


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "UTC", "America/New_York"])
def test_an_iana_timezone_is_accepted(client, zone):
    resp = client.patch("/me/settings", headers=_bearer(_new_token()), json={"timezone": zone})
    assert resp.status_code == 200 and resp.json()["timezone"] == zone


@pytest.mark.parametrize("zone", ["Mars/Olympus", "+05:30", "IST", "Asia/Kolkataa", ""])
def test_a_timezone_that_is_not_an_iana_name_is_refused(client, zone):
    """Quiet hours are interpreted against this with ZoneInfo at push time; a
    value it cannot resolve would raise inside a push, not here."""
    resp = client.patch("/me/settings", headers=_bearer(_new_token()), json={"timezone": zone})
    assert resp.status_code == 422


def test_a_timezone_can_be_cleared(client):
    token = _new_token()
    client.patch("/me/settings", headers=_bearer(token), json={"timezone": "Asia/Kolkata"})

    client.patch("/me/settings", headers=_bearer(token), json={"timezone": None})

    assert client.get("/me/settings", headers=_bearer(token)).json()["timezone"] is None


def test_a_user_can_only_change_their_own_settings(client):
    a, b = _new_token(), _new_token()
    client.patch("/me/settings", headers=_bearer(a), json={"tts_on": False})

    assert client.get("/me/settings", headers=_bearer(b)).json()["tts_on"] is True


# --- the reason it exists -----------------------------------------------------------------------


def test_the_orchestrator_renders_into_the_language_a_recipient_chose(client, db_session):
    """Until now every user was Telugu, so a receiver who picked Hindi in the
    app got no Hindi rendering. The pipeline reads users.preferred_language."""
    from app.db.repository import create_message
    from app.pipeline import _target_languages

    author = DbUser(name="Author", preferred_language="te")
    db_session.add(author)
    db_session.flush()
    recipient_token = _new_token()
    client.patch(
        "/me/settings", headers=_bearer(recipient_token), json={"preferred_language": "hi"}
    )
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=uuid.UUID(recipient_token),
        kind="text",
        text="hello",
        source_lang="te",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()

    assert _target_languages(db_session, message, "te") == ["hi"]


def test_quiet_hours_set_through_the_endpoint_suppress_a_push_inside_the_window(client, db_session):
    """The read side has existed since Month 1; this is the first write path."""
    token = _new_token()
    client.patch(
        "/me/settings",
        headers=_bearer(token),
        json={
            "quiet_hours_start": "22:00:00",
            "quiet_hours_end": "07:00:00",
            "timezone": "Asia/Kolkata",
        },
    )
    row = _row(db_session, token)
    # 17:30 UTC is 23:00 in Kolkata (inside 22:00-07:00); 02:30 UTC is 08:00 (outside).
    inside = datetime(2026, 10, 4, 17, 30, tzinfo=UTC)
    outside = datetime(2026, 10, 4, 2, 30, tzinfo=UTC)

    def quiet(now):
        return is_quiet_hours(
            timezone=row.timezone,
            quiet_hours_start=row.quiet_hours_start,
            quiet_hours_end=row.quiet_hours_end,
            now=now,
        )

    assert quiet(inside) is True
    assert quiet(outside) is False


def test_quiet_hours_without_a_timezone_are_stored_but_never_enforced(client, db_session):
    """Documents a trap rather than endorsing it: push treats a user with no
    timezone as 'cannot compute quiet hours' and never suppresses
    (is_quiet_hours returns False). The elder app does not send a timezone, so
    until it does, a window a person carefully saved has no effect."""
    token = _new_token()
    client.patch(
        "/me/settings",
        headers=_bearer(token),
        json={"quiet_hours_start": "00:00:00", "quiet_hours_end": "23:59:00"},
    )
    row = _row(db_session, token)

    assert row.quiet_hours_start == time(0, 0)
    assert (
        is_quiet_hours(
            timezone=row.timezone,
            quiet_hours_start=row.quiet_hours_start,
            quiet_hours_end=row.quiet_hours_end,
            now=datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
        )
        is False
    )
