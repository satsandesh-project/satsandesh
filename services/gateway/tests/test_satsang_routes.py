"""The scheduled satsang broadcast routes (`/sessions*`, app/satsang.py, contracts/chat/satsang.py).

Real signed tokens (`issue_token`), so the whole auth path runs: the role is the DATABASE's, read
per request. Three groups worth reading first:

- **The stream key is a credential.** It is shown once, by create and by rotate; the database holds
  only its SHA-256, so there is no route that could read it back. Every "no key" assertion here is on
  the SERIALIZED response text, not on a model, because OPEN_QUESTIONS #25 (Week 7) was a read path
  that returned something it held and no test looked at what actually went out.
- **A non-owner is told the session does not exist** (404), exactly as `app/media.py` does for held
  audio: a 403 would confirm a guessable id is real.
- **The ingest hooks are called by the RTMP server, not a browser.** They are authenticated by a
  shared secret, not by any user token (see the module docstring in app/satsang.py for why that is
  what is possible, and what it does not stop). No user token, not even an admin's, opens them.
"""

import hashlib
import importlib
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from contracts.chat.satsang import (
    SatsangSessionAdminList,
    SatsangSessionCreated,
    SatsangSessionList,
    SatsangSessionOut,
    SatsangStatus,
)
from sqlalchemy import select, text

from app.config import get_settings
from app.db.models import User as DbUser
from app.db.satsang import SatsangSession
from app.tokens import issue_token

HOOK_SECRET = "hook-secret-for-the-tests-only"
PUBLISH = "/sessions/ingest/publish"
DONE = "/sessions/ingest/done"


def _user(db_session, name, role="elder"):
    user = DbUser(name=name, preferred_language="te", role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _as(user):
    return {"Authorization": f"Bearer {issue_token(user.id)}"}


def _legacy(user):
    """What the elder app sends on staging today: the bare UUID is the credential."""
    return {"Authorization": f"Bearer {user.id}"}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def admin(db_session):
    return _user(db_session, "Asha", "admin")


@pytest.fixture
def moderator(db_session):
    return _user(db_session, "Mohan", "moderator")


@pytest.fixture
def elder(db_session):
    return _user(db_session, "Anand")


@pytest.fixture
def swami(db_session):
    """The speaker: an ordinary user, who is not an admin or a moderator."""
    return _user(db_session, "Swami Ananda")


@pytest.fixture
def hook(monkeypatch):
    monkeypatch.setattr(get_settings(), "SATSANG_HOOK_SECRET", HOOK_SECRET)
    return {"secret": HOOK_SECRET}


def _schedule(client, who, speaker, *, minutes=120, title="Thursday discourse"):
    resp = client.post(
        "/sessions",
        json={
            "title": title,
            "speaker_user_id": str(speaker.id),
            "scheduled_start_at": (_now() + timedelta(minutes=minutes)).isoformat(),
        },
        headers=_as(who),
    )
    assert resp.status_code == 201, resp.text
    return SatsangSessionCreated.model_validate(resp.json())


def _insert(db_session, speaker, creator, *, status="scheduled", minutes=120, key=None):
    """A row made directly, with a key we know, for tests that are about something other than
    creation. Returns (row, plaintext key)."""
    key = key or f"key-{status}-{minutes}-{speaker.id}"
    started = _now() - timedelta(hours=1) if status in ("live", "ended") else None
    ended = _now() if status == "ended" else None
    row = SatsangSession(
        title=f"{status} satsang",
        speaker_user_id=speaker.id,
        scheduled_start_at=_now() + timedelta(minutes=minutes),
        status=status,
        started_at=started,
        ended_at=ended,
        stream_key_sha256=_hash(key),
        created_by=creator.id,
    )
    db_session.add(row)
    db_session.commit()
    return row, key


def _fresh(db_session, row_id) -> SatsangSession:
    db_session.expire_all()
    return db_session.get(SatsangSession, row_id)


def _snapshot(db_session):
    db_session.expire_all()
    rows = db_session.execute(select(SatsangSession).order_by(SatsangSession.title)).scalars()
    return [(r.id, r.status, r.started_at, r.ended_at) for r in rows]


def _publish(client, sid, key, secret=HOOK_SECRET):
    """Exactly what nginx-rtmp posts for rtmp://host/live/<session id>?key=<key> (measured on Debian's
    libnginx-mod-rtmp 1.2.2): the stream NAME is the session id, the key is a separate field."""
    return client.post(PUBLISH, params={"secret": secret}, data={"name": str(sid), "key": key})


def _done(client, sid, key, secret=HOOK_SECRET):
    return client.post(DONE, params={"secret": secret}, data={"name": str(sid), "key": key})


# --- who may schedule ----------------------------------------------------------------------------


def test_an_admin_schedules_a_satsang_and_the_database_keeps_only_a_hash(
    client, db_session, admin, swami
) -> None:
    made = _schedule(client, admin, swami)
    assert made.session.status is SatsangStatus.SCHEDULED
    assert made.session.speaker_name == "Swami Ananda"
    assert made.session.created_by == str(admin.id)
    assert made.stream_key and made.stream_key in made.ingest_url
    # Named by the SESSION, key as an argument: nginx-rtmp names the HLS files after the stream
    # name, so named by the key, every listener's URL would carry it.
    assert made.ingest_url == (
        f"rtmp://localhost:1935/live/{made.session.id}?key={made.stream_key}"
    )
    row = _fresh(db_session, made.session.id)
    assert row.stream_key_sha256 == _hash(made.stream_key)
    # Nothing in the stored row, rendered as text, contains the key itself.
    stored = db_session.execute(
        text("SELECT t::text FROM sessions t WHERE id = :id"), {"id": row.id}
    ).scalar_one()
    assert made.stream_key not in stored


def test_a_moderator_may_schedule_too(client, moderator, swami) -> None:
    assert _schedule(client, moderator, swami).session.created_by == str(moderator.id)


def test_a_plain_user_is_refused_with_403_not_500_and_nothing_is_stored(
    client, db_session, elder, swami
) -> None:
    resp = client.post(
        "/sessions",
        json={
            "title": "t",
            "speaker_user_id": str(swami.id),
            "scheduled_start_at": (_now() + timedelta(hours=2)).isoformat(),
        },
        headers=_as(elder),
    )
    assert resp.status_code == 403
    assert db_session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


def test_scheduling_needs_a_signed_token_a_bare_uuid_is_not_enough(client, admin, swami) -> None:
    """Same rule, same reason, as /admin and /moderation: in legacy mode a bare UUID IS that user,
    so anyone who knew an admin's id could otherwise decide who broadcasts."""
    body = {
        "title": "t",
        "speaker_user_id": str(swami.id),
        "scheduled_start_at": (_now() + timedelta(hours=2)).isoformat(),
    }
    assert client.post("/sessions", json=body, headers=_legacy(admin)).status_code == 401
    assert client.post("/sessions", json=body).status_code == 401


def test_a_speaker_who_does_not_exist_and_a_start_in_the_past_are_refused(
    client, db_session, admin, swami
) -> None:
    import uuid

    when = (_now() + timedelta(hours=2)).isoformat()
    ghost = client.post(
        "/sessions",
        json={"title": "t", "speaker_user_id": str(uuid.uuid4()), "scheduled_start_at": when},
        headers=_as(admin),
    )
    assert ghost.status_code == 404
    past = client.post(
        "/sessions",
        json={
            "title": "t",
            "speaker_user_id": str(swami.id),
            "scheduled_start_at": (_now() - timedelta(hours=1)).isoformat(),
        },
        headers=_as(admin),
    )
    assert past.status_code == 422
    assert db_session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


# --- listing: a plain user sees the upcoming ones, and never a key ---------------------------------


def test_a_plain_user_lists_upcoming_sessions_and_no_key_is_in_the_response_text(
    client, db_session, admin, swami, elder
) -> None:
    mine = _schedule(client, admin, swami, title="Swami's own satsang")
    other = _schedule(client, admin, elder, title="Anand's satsang", minutes=180)

    # Listed as the SPEAKER, whose own session this is -- the strictest case for "no key".
    for who in (swami, elder):
        resp = client.get("/sessions", headers=_as(who))
        assert resp.status_code == 200
        listing = SatsangSessionList.model_validate(resp.json())
        assert [i.title for i in listing.items] == ["Swami's own satsang", "Anand's satsang"]
        serialized = resp.text
        for secret in (mine.stream_key, other.stream_key):
            assert secret not in serialized
            assert _hash(secret) not in serialized
        assert "stream_key" not in serialized and "ingest_url" not in serialized
        # an elder is told the speaker's name, never their id
        assert str(swami.id) not in serialized and "speaker_user_id" not in serialized


def test_an_elder_with_the_legacy_token_the_app_uses_today_can_still_list(
    client, db_session, admin, swami, elder
) -> None:
    _schedule(client, admin, swami)
    resp = client.get("/sessions", headers=_legacy(elder))
    assert resp.status_code == 200 and len(resp.json()["items"]) == 1


def test_the_listing_shows_what_is_coming_what_is_live_and_what_was_called_off(
    client, db_session, admin, swami
) -> None:
    _insert(db_session, swami, admin, status="scheduled", minutes=300, key="k-later")
    _insert(db_session, swami, admin, status="scheduled", minutes=60, key="k-sooner")
    live, _ = _insert(db_session, swami, admin, status="live", minutes=-30, key="k-live")
    _insert(db_session, swami, admin, status="cancelled", minutes=120, key="k-off")
    _insert(db_session, swami, admin, status="ended", minutes=-300, key="k-over")
    # A no-show from three days ago and an old cancellation are not "upcoming".
    _insert(db_session, swami, admin, status="scheduled", minutes=-3 * 24 * 60, key="k-noshow")
    _insert(db_session, swami, admin, status="cancelled", minutes=-3 * 24 * 60, key="k-oldoff")

    items = SatsangSessionList.model_validate(client.get("/sessions", headers=_as(swami)).json())
    assert [(i.status.value, i.title) for i in items.items] == [
        ("live", "live satsang"),
        ("scheduled", "scheduled satsang"),
        ("cancelled", "cancelled satsang"),
        ("scheduled", "scheduled satsang"),
    ]
    only_live = [i for i in items.items if i.status is SatsangStatus.LIVE]
    assert only_live[0].hls_url == f"/hls/{live.id}.m3u8"
    assert all(i.hls_url is None for i in items.items if i.status is not SatsangStatus.LIVE)


def test_the_console_listing_is_for_moderators_shows_every_status_and_carries_no_key(
    client, db_session, admin, moderator, swami, elder
) -> None:
    made = _schedule(client, admin, swami)
    _insert(db_session, swami, admin, status="ended", minutes=-300, key="k-over")
    assert client.get("/sessions/manage", headers=_as(elder)).status_code == 403
    assert client.get("/sessions/manage", headers=_legacy(admin)).status_code == 401
    resp = client.get("/sessions/manage", headers=_as(moderator))
    assert resp.status_code == 200
    listing = SatsangSessionAdminList.model_validate(resp.json())
    assert {s.status for s in listing.items} == {SatsangStatus.SCHEDULED, SatsangStatus.ENDED}
    assert made.stream_key not in resp.text and "stream_key" not in resp.text


def test_no_get_route_under_sessions_returns_a_key(client, db_session, admin, swami) -> None:
    """Enumerated from the OpenAPI schema, so a GET added later is covered without anyone
    remembering to come back here. (Not from `app.routes`: include_router appends one opaque object
    there in this FastAPI, so every router-mounted path is invisible to it.)"""
    from app.main import app

    made = _schedule(client, admin, swami)
    gets = sorted(
        p for p, ops in app.openapi()["paths"].items() if p.startswith("/sessions") and "get" in ops
    )
    assert gets, "no GET found under /sessions: this test would be checking nothing"
    for path in gets:
        resp = client.get(path.replace("{session_id}", made.session.id), headers=_as(admin))
        # The reminder routes answer 404 until the reminder has been SENT (a freshly scheduled satsang
        # has none yet); a sent one is checked for keys and ids in tests/test_satsang_reminder_delivery.py.
        expected = 404 if path.endswith(("/reminder", "/reminder/audio")) else 200
        assert resp.status_code == expected, (path, resp.text)
        assert made.stream_key not in resp.text, path
        assert "stream_key" not in resp.text, path


# --- the key: only its owner (and a moderator) can be given a new one -------------------------------


def test_the_speaker_can_get_a_fresh_key_and_it_is_the_one_that_works(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)  # inside the go-live window
    resp = client.post(f"/sessions/{made.session.id}/key", headers=_as(swami))
    assert resp.status_code == 200
    rotated = SatsangSessionCreated.model_validate(resp.json())
    assert rotated.stream_key != made.stream_key
    assert _fresh(db_session, made.session.id).stream_key_sha256 == _hash(rotated.stream_key)
    assert (
        _publish(client, made.session.id, made.stream_key).status_code == 403
    )  # the old one is dead
    assert _publish(client, rotated.session.id, rotated.stream_key).status_code == 200


def test_a_moderator_may_rotate_a_key_too_which_is_how_a_leaked_one_is_dealt_with(
    client, admin, moderator, swami
) -> None:
    made = _schedule(client, admin, swami)
    assert (
        client.post(f"/sessions/{made.session.id}/key", headers=_as(moderator)).status_code == 200
    )


def test_anyone_else_asking_for_the_key_is_told_the_session_does_not_exist(
    client, db_session, admin, swami, elder
) -> None:
    """404, not 403, matching app/media.py for held audio ("not found and not-allowed are
    deliberately one response: a 403 would confirm the guessable id is real"). Indistinguishable
    from a session that is not there -- body included."""
    import uuid

    made = _schedule(client, admin, swami)
    before = _fresh(db_session, made.session.id).stream_key_sha256
    not_yours = client.post(f"/sessions/{made.session.id}/key", headers=_as(elder))
    missing = client.post(f"/sessions/{uuid.uuid4()}/key", headers=_as(elder))
    garbage = client.post("/sessions/not-a-uuid/key", headers=_as(elder))
    assert not_yours.status_code == missing.status_code == garbage.status_code == 404
    assert not_yours.json() == missing.json() == garbage.json()
    assert made.stream_key not in not_yours.text
    assert _fresh(db_session, made.session.id).stream_key_sha256 == before  # and nothing rotated


def test_the_key_route_needs_a_signed_token(client, admin, swami) -> None:
    made = _schedule(client, admin, swami)
    assert client.post(f"/sessions/{made.session.id}/key").status_code == 401
    assert (
        client.post(f"/sessions/{made.session.id}/key", headers=_legacy(swami)).status_code == 401
    )


def test_a_live_ended_or_cancelled_satsang_cannot_have_its_key_rotated(
    client, db_session, admin, swami
) -> None:
    """Live: rotating would orphan the running stream, whose end-of-stream hook would then carry a
    key nobody recognises, leaving the satsang live forever."""
    for status in ("live", "ended", "cancelled"):
        row, _ = _insert(db_session, swami, admin, status=status, key=f"k-{status}")
        resp = client.post(f"/sessions/{row.id}/key", headers=_as(swami))
        assert resp.status_code == 409, status


def test_two_satsangs_never_end_up_with_the_same_key(client, admin, swami, monkeypatch) -> None:
    """If the generator ever repeats itself, the second create retries rather than 500ing on the
    unique index, and the two sessions still hold different keys."""
    satsang = importlib.import_module("app.satsang")
    keys = iter(["repeated-key", "repeated-key", "repeated-key", "a-different-key"])
    monkeypatch.setattr(satsang, "_new_stream_key", lambda: next(keys))
    first = _schedule(client, admin, swami, title="first")
    second = _schedule(client, admin, swami, title="second")
    assert first.stream_key == "repeated-key"
    assert second.stream_key == "a-different-key"


def test_a_generator_that_only_ever_repeats_is_a_503_and_stores_nothing_new(
    client, db_session, admin, swami, monkeypatch
) -> None:
    satsang = importlib.import_module("app.satsang")
    monkeypatch.setattr(satsang, "_new_stream_key", lambda: "always-the-same")
    _schedule(client, admin, swami, title="first")
    resp = client.post(
        "/sessions",
        json={
            "title": "second",
            "speaker_user_id": str(swami.id),
            "scheduled_start_at": (_now() + timedelta(hours=2)).isoformat(),
        },
        headers=_as(admin),
    )
    assert resp.status_code == 503
    assert db_session.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 1


# --- status transitions at the API ------------------------------------------------------------------


def test_a_moderator_cancels_a_scheduled_satsang_only(client, db_session, admin, moderator, swami):
    scheduled, _ = _insert(db_session, swami, admin, key="k1")
    resp = client.post(f"/sessions/{scheduled.id}/cancel", headers=_as(moderator))
    assert resp.status_code == 200
    assert SatsangSessionOut.model_validate(resp.json()).status is SatsangStatus.CANCELLED
    for status in ("live", "ended", "cancelled"):
        row, _ = _insert(db_session, swami, admin, status=status, key=f"k-{status}")
        before = _snapshot(db_session)
        assert client.post(f"/sessions/{row.id}/cancel", headers=_as(moderator)).status_code == 409
        assert _snapshot(db_session) == before, status


def test_a_moderator_ends_a_live_satsang_and_nothing_else_can_be_ended(
    client, db_session, admin, moderator, swami
) -> None:
    live, _ = _insert(db_session, swami, admin, status="live", minutes=-30, key="k-live")
    resp = client.post(f"/sessions/{live.id}/end", headers=_as(moderator))
    assert resp.status_code == 200
    ended = SatsangSessionOut.model_validate(resp.json())
    assert ended.status is SatsangStatus.ENDED and ended.ended_at is not None
    # scheduled -> ended without ever being live, and ended/cancelled -> ended, are all refused.
    for status in ("scheduled", "ended", "cancelled"):
        row, _ = _insert(db_session, swami, admin, status=status, key=f"k-{status}")
        before = _snapshot(db_session)
        assert client.post(f"/sessions/{row.id}/end", headers=_as(moderator)).status_code == 409
        assert _snapshot(db_session) == before, status


def test_cancelling_and_ending_are_for_moderators_and_a_missing_id_is_404(
    client, db_session, admin, moderator, swami, elder
) -> None:
    import uuid

    row, _ = _insert(db_session, swami, admin, key="k1")
    for action in ("cancel", "end"):
        assert client.post(f"/sessions/{row.id}/{action}", headers=_as(elder)).status_code == 403
        assert (
            client.post(f"/sessions/{row.id}/{action}", headers=_legacy(admin)).status_code == 401
        )
        missing = client.post(f"/sessions/{uuid.uuid4()}/{action}", headers=_as(moderator))
        assert missing.status_code == 404
    assert _fresh(db_session, row.id).status == "scheduled"


# --- the ingest hooks: what the RTMP server calls ---------------------------------------------------


def test_publishing_with_a_valid_key_puts_a_scheduled_satsang_on_the_air(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)  # inside the go-live window
    resp = _publish(client, made.session.id, made.stream_key)
    assert 200 <= resp.status_code < 300, resp.text
    row = _fresh(db_session, made.session.id)
    assert row.status == "live" and row.started_at is not None and row.ended_at is None
    assert made.stream_key not in resp.text


@pytest.mark.parametrize("name", ["", "   ", "not-a-key", "x" * 40])
def test_an_unknown_or_empty_key_is_refused_and_changes_nothing(
    client, db_session, admin, swami, hook, name
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    resp = _publish(client, made.session.id, name)
    assert resp.status_code == 403
    assert _snapshot(db_session) == before


def test_a_request_with_no_key_field_at_all_is_refused_and_changes_nothing(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    assert client.post(PUBLISH, params={"secret": HOOK_SECRET}, data={}).status_code == 403
    assert client.post(PUBLISH, params={"secret": HOOK_SECRET}).status_code == 403
    no_key = {"name": made.session.id}
    no_name = {"key": made.stream_key}
    assert client.post(PUBLISH, params={"secret": HOOK_SECRET}, data=no_key).status_code == 403
    assert client.post(PUBLISH, params={"secret": HOOK_SECRET}, data=no_name).status_code == 403
    assert _snapshot(db_session) == before


@pytest.mark.parametrize("status", ["ended", "cancelled"])
def test_the_key_of_a_finished_or_cancelled_satsang_is_dead(
    client, db_session, admin, swami, hook, status
) -> None:
    row, key = _insert(db_session, swami, admin, status=status, minutes=-30, key=f"k-{status}")
    before = _snapshot(db_session)
    assert _publish(client, row.id, key).status_code == 403
    assert _snapshot(db_session) == before
    assert _fresh(db_session, row.id).status == status  # ended -> live never happens


def test_only_one_satsang_goes_live_and_the_refused_one_stays_scheduled(
    client, db_session, admin, swami, hook
) -> None:
    first = _schedule(client, admin, swami, title="first", minutes=10)
    second = _schedule(client, admin, swami, title="second", minutes=20)
    assert _publish(client, first.session.id, first.stream_key).status_code == 200
    assert _publish(client, second.session.id, second.stream_key).status_code == 403
    assert _fresh(db_session, second.session.id).status == "scheduled"
    live = db_session.execute(text("SELECT count(*) FROM sessions WHERE status = 'live'"))
    assert live.scalar_one() == 1


def test_going_live_more_than_an_hour_early_is_refused_but_arbitrarily_late_is_not(
    client, db_session, admin, swami, hook
) -> None:
    early, early_key = _insert(db_session, swami, admin, minutes=180, key="k-early")
    assert _publish(client, early.id, early_key).status_code == 403
    assert _fresh(db_session, early.id).status == "scheduled"
    late, late_key = _insert(db_session, swami, admin, minutes=-4 * 60, key="k-late")
    assert (
        _publish(client, late.id, late_key).status_code == 200
    )  # a speaker four hours behind is still on
    assert _fresh(db_session, late.id).status == "live"


def test_a_second_publish_while_live_is_refused_and_changes_nothing(
    client, db_session, admin, swami, hook
) -> None:
    """Measured on the real media server (OPEN_QUESTIONS #37): a second publish to a stream that is
    already being published is refused by nginx-rtmp ("already publishing"), and nginx still sends
    the end-of-stream hook for that refused connection, which used to end the live satsang. Refusing
    it HERE, at the publish hook, means nginx never starts that connection, so it sends no done."""
    made = _schedule(client, admin, swami, minutes=5)
    assert _publish(client, made.session.id, made.stream_key).status_code == 200
    started = _fresh(db_session, made.session.id).started_at
    assert _publish(client, made.session.id, made.stream_key).status_code == 403
    after = _fresh(db_session, made.session.id)
    assert (after.status, after.started_at, after.ended_at) == ("live", started, None)


def test_the_publisher_that_is_actually_on_the_air_can_still_end_it_after_a_refused_second(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    assert _publish(client, made.session.id, made.stream_key).status_code == 200
    assert _publish(client, made.session.id, made.stream_key).status_code == 403
    assert _done(client, made.session.id, made.stream_key).status_code == 200
    assert _fresh(db_session, made.session.id).status == "ended"


def test_a_refused_second_publish_does_not_put_the_key_in_the_log(
    client, db_session, admin, swami, hook, caplog
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    assert _publish(client, made.session.id, made.stream_key).status_code == 200
    with caplog.at_level("DEBUG"):
        assert _publish(client, made.session.id, made.stream_key).status_code == 403
    assert made.stream_key not in caplog.text


def test_the_json_form_the_mock_documents_is_accepted_too(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    resp = client.post(
        PUBLISH,
        params={"secret": HOOK_SECRET},
        json={"name": made.session.id, "key": made.stream_key},
    )
    assert resp.status_code == 200
    assert _fresh(db_session, made.session.id).status == "live"


def test_the_speaker_stopping_ends_the_satsang_and_frees_the_air(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    _publish(client, made.session.id, made.stream_key)
    resp = _done(client, made.session.id, made.stream_key)
    assert resp.status_code == 200
    row = _fresh(db_session, made.session.id)
    assert row.status == "ended" and row.ended_at is not None
    # the key is now dead, and a different satsang may go on
    assert _publish(client, made.session.id, made.stream_key).status_code == 403
    nxt = _schedule(client, admin, swami, title="next", minutes=5)
    assert _publish(client, nxt.session.id, nxt.stream_key).status_code == 200


def test_the_end_of_stream_hook_refuses_an_unknown_key_and_a_satsang_that_is_not_live(
    client, db_session, admin, swami, hook
) -> None:
    scheduled = _schedule(client, admin, swami)
    before = _snapshot(db_session)
    assert _done(client, scheduled.session.id, "not-a-key").status_code == 403
    assert (
        _done(client, scheduled.session.id, scheduled.stream_key).status_code == 409
    )  # scheduled -> ended: no
    assert _snapshot(db_session) == before


# --- the hooks' own authentication: this is the adversarial surface ----------------------------------


def test_no_user_token_opens_the_hook_not_even_an_admins(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    for headers in (_as(admin), _as(swami), _legacy(admin), {}):
        fields = {"name": made.session.id, "key": made.stream_key}
        resp = client.post(PUBLISH, data=fields, headers=headers)
        assert resp.status_code == 403, headers
    assert _snapshot(db_session) == before


@pytest.mark.parametrize("secret", ["", "wrong", HOOK_SECRET + "x", HOOK_SECRET[:-1]])
def test_a_wrong_or_missing_secret_is_refused_even_with_a_perfectly_valid_key(
    client, db_session, admin, swami, hook, secret
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    assert _publish(client, made.session.id, made.stream_key, secret=secret).status_code == 403
    fields = {"name": made.session.id, "key": made.stream_key}
    assert client.post(PUBLISH, data=fields).status_code == 403
    assert _done(client, made.session.id, made.stream_key, secret=secret).status_code == 403
    assert _snapshot(db_session) == before


def test_with_no_secret_configured_the_hooks_refuse_everyone_nobody_can_go_live(
    client, db_session, admin, swami, monkeypatch
) -> None:
    """Fail closed: an unconfigured gateway must not accept a broadcast, and must not accept an
    empty secret as if it matched an empty setting."""
    monkeypatch.setattr(get_settings(), "SATSANG_HOOK_SECRET", None)
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    assert _publish(client, made.session.id, made.stream_key, secret="anything").status_code == 503
    assert _publish(client, made.session.id, made.stream_key, secret="").status_code == 503
    assert _done(client, made.session.id, made.stream_key, secret="anything").status_code == 503
    assert _snapshot(db_session) == before


def test_an_oversized_hook_body_is_refused_before_it_is_read_and_changes_nothing(
    client, db_session, admin, swami, hook
) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    huge = f"name={made.session.id}&key={made.stream_key}&pad=".encode() + b"x" * 200_000
    resp = client.post(
        PUBLISH,
        params={"secret": HOOK_SECRET},
        content=huge,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 413
    assert _snapshot(db_session) == before


def test_the_key_is_never_written_to_the_log(
    client, db_session, admin, swami, hook, caplog
) -> None:
    with caplog.at_level(logging.DEBUG):
        made = _schedule(client, admin, swami, minutes=5)
        _publish(client, made.session.id, made.stream_key)
        _publish(client, made.session.id, "a-wrong-key-that-must-not-be-logged-either")
        _done(client, made.session.id, made.stream_key)
        client.post(f"/sessions/{made.session.id}/key", headers=_as(swami))
    # Only the APP's loggers: the test client's own HTTP logger prints the URL it called, query
    # string and all, which says nothing about what the gateway writes.
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app"))
    assert made.stream_key not in logged and _hash(made.stream_key) not in logged
    assert "a-wrong-key-that-must-not-be-logged-either" not in logged
    assert HOOK_SECRET not in logged
    # A user id is a bearer credential in legacy auth mode: the log names sessions, not people.
    assert str(admin.id) not in logged and str(swami.id) not in logged


def test_the_hook_response_carries_no_key(client, admin, swami, hook) -> None:
    made = _schedule(client, admin, swami, minutes=5)
    for resp in (
        _publish(client, made.session.id, made.stream_key),
        _done(client, made.session.id, made.stream_key),
    ):
        assert made.stream_key not in resp.text
        assert "stream_key" not in json.dumps(resp.json())


# --- the stream is named by the session, never by the key -----------------------------------------


def test_a_valid_key_under_another_sessions_name_is_refused_and_changes_nothing(
    client, db_session, admin, swami, hook
) -> None:
    mine = _schedule(client, admin, swami, title="mine", minutes=5)
    other = _schedule(client, admin, swami, title="other", minutes=5)
    before = _snapshot(db_session)
    assert _publish(client, other.session.id, mine.stream_key).status_code == 403
    assert _done(client, other.session.id, mine.stream_key).status_code == 403
    assert _snapshot(db_session) == before


def test_publishing_with_the_key_as_the_stream_name_is_refused(
    client, db_session, admin, swami, hook
) -> None:
    """The old way, rtmp://host/live/<key>. Refused even with the right key in the key field, so the
    RTMP server can never write HLS files whose names are the key."""
    made = _schedule(client, admin, swami, minutes=5)
    before = _snapshot(db_session)
    assert _publish(client, made.stream_key, made.stream_key).status_code == 403
    assert _snapshot(db_session) == before
