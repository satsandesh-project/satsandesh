"""The satsang broadcast in the mock chat gateway: what the elder app builds against.

Same rules as the real routes will have -- only a moderator schedules, cancels and rotates;
exactly one satsang is live at a time; the key is what authorises going on air and is returned
exactly once; and an elder is never told the key, the speaker's internal id, or our bookkeeping.

`test_the_key_never_appears_in_any_other_response` is the one to read: it takes the real key out
of the create response and then asserts that string appears in **no** other response body. That
is the check OPEN_QUESTIONS #25 was missing -- not "the admin path works", but "the thing we must
not hand out is not in anything we hand out".
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from contracts.chat.mock import app as mock_app
from contracts.chat.mock import satsang as mock_satsang
from contracts.chat.satsang import (
    SatsangSessionAdminList,
    SatsangSessionCreated,
    SatsangSessionList,
    SatsangSessionOut,
    SatsangStatus,
)
from fastapi.testclient import TestClient

MOD = {"X-Mock-User-Id": "moderator-1", "X-Mock-Role": "moderator"}
ADMIN = {"X-Mock-User-Id": "admin-1", "X-Mock-Role": "admin"}
ELDER = {"X-Mock-User-Id": "anand"}


@pytest.fixture
def client():
    with TestClient(mock_app.app) as test_client:
        yield test_client
    mock_satsang.reset()


def _in(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _schedule(client, *, minutes: int = 10, headers=MOD, title="Thursday discourse"):
    resp = client.post(
        "/sessions",
        json={
            "title": title,
            "speaker_user_id": "user-swami-1",
            "scheduled_start_at": _in(minutes),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return SatsangSessionCreated.model_validate(resp.json())


def _publish(client, key: str):
    return client.post("/sessions/ingest/publish", json={"stream_key": key})


# --- who may schedule ---------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [ELDER, {"X-Mock-User-Id": "stranger", "X-Mock-Role": "elder"}])
def test_only_a_moderator_or_admin_schedules_a_satsang(client, headers) -> None:
    resp = client.post(
        "/sessions",
        json={"title": "t", "speaker_user_id": "u", "scheduled_start_at": _in(30)},
        headers=headers,
    )
    assert resp.status_code == 403
    assert mock_satsang._sessions == {}


def test_a_new_satsang_is_scheduled_has_not_started_and_returns_its_key_once(client) -> None:
    made = _schedule(client, headers=ADMIN)
    assert made.session.status is SatsangStatus.SCHEDULED
    assert made.session.started_at is None and made.session.ended_at is None
    assert made.session.reminder_sent_at is None
    assert made.stream_key and made.stream_key in made.ingest_url
    assert made.ingest_url.startswith("rtmp://")


def test_a_start_time_without_a_timezone_is_refused_by_the_route(client) -> None:
    resp = client.post(
        "/sessions",
        json={"title": "t", "speaker_user_id": "u", "scheduled_start_at": "2026-10-15T19:00:00"},
        headers=MOD,
    )
    assert resp.status_code == 422


# --- the key is a credential --------------------------------------------------------------------


def test_the_key_never_appears_in_any_other_response(client) -> None:
    """The check #25 was missing: not "the admin path works" but "the secret is in nothing else"."""
    made = _schedule(client)
    key = made.stream_key
    session_id = made.session.id

    reads = {
        "elder listing": client.get("/sessions", headers=ELDER),
        "console listing": client.get("/sessions/manage", headers=MOD),
        "cancel": client.post(f"/sessions/{session_id}/cancel", headers=MOD),
    }
    for label, resp in reads.items():
        assert resp.status_code == 200, (label, resp.text)
        assert key not in json.dumps(resp.json()), f"the stream key leaked through {label}"
        assert "stream_key" not in json.dumps(resp.json()), label


def test_an_elder_listing_carries_no_key_no_speaker_id_and_no_bookkeeping(client) -> None:
    _schedule(client)
    listing = SatsangSessionList.model_validate(client.get("/sessions", headers=ELDER).json())
    assert len(listing.items) == 1
    raw = json.dumps(listing.model_dump(mode="json"))
    for leaked in ("stream_key", "speaker_user_id", "reminder_sent_at", "created_by", "ingest_url"):
        assert leaked not in raw, leaked
    assert listing.items[0].speaker_name


def test_no_get_route_under_sessions_reads_a_key_back(client) -> None:
    """By design: the real gateway stores only the SHA-256 and could not answer such a route.

    Enumerated from the OpenAPI schema, so a GET added later is covered without anyone
    remembering to come back here. **Not** from `app.routes`: in FastAPI 0.143 `include_router`
    appends one opaque `_IncludedRouter` object rather than flattening the child routes, so every
    router-mounted path is invisible there -- an earlier version of this test looked there, found
    nothing, and would have passed while checking nothing. Hence the guard below."""
    made = _schedule(client)
    schema = mock_app.app.openapi()["paths"]
    gets = sorted(p for p, ops in schema.items() if p.startswith("/sessions") and "get" in ops)
    assert gets, "no GET found under /sessions: this test would be checking nothing"
    for path in gets:
        resp = client.get(path.replace("{session_id}", made.session.id), headers=MOD)
        assert resp.status_code == 200, (path, resp.text)
        assert made.stream_key not in json.dumps(resp.json()), path


def test_rotating_the_key_gives_a_new_one_and_retires_the_old(client) -> None:
    made = _schedule(client)
    rotated = SatsangSessionCreated.model_validate(
        client.post(f"/sessions/{made.session.id}/key", headers=MOD).json()
    )
    assert rotated.stream_key != made.stream_key
    assert _publish(client, made.stream_key).status_code == 403  # the old key is dead
    assert _publish(client, rotated.stream_key).status_code == 200


def test_only_a_moderator_rotates_or_cancels(client) -> None:
    made = _schedule(client)
    assert client.post(f"/sessions/{made.session.id}/key", headers=ELDER).status_code == 403
    assert client.post(f"/sessions/{made.session.id}/cancel", headers=ELDER).status_code == 403


# --- going live ---------------------------------------------------------------------------------


def test_the_key_is_what_puts_a_satsang_on_air(client) -> None:
    made = _schedule(client)
    assert client.get("/sessions", headers=ELDER).json()["items"][0]["hls_url"] is None

    live = SatsangSessionOut.model_validate(_publish(client, made.stream_key).json())
    assert live.status is SatsangStatus.LIVE and live.started_at is not None

    item = client.get("/sessions", headers=ELDER).json()["items"][0]
    assert item["status"] == "live" and item["hls_url"] == f"/hls/{made.session.id}.m3u8"


def test_a_wrong_key_is_refused_without_saying_whether_any_session_has_it(client) -> None:
    _schedule(client)
    resp = _publish(client, "not-a-real-key")
    assert resp.status_code == 403
    assert "not found" not in resp.text.lower()


def test_only_one_satsang_is_live_at_a_time(client) -> None:
    first = _schedule(client, title="First")
    second = _schedule(client, title="Second")
    assert _publish(client, first.stream_key).status_code == 200
    assert _publish(client, second.stream_key).status_code == 409
    assert sum(1 for r in mock_satsang._sessions.values() if r["status"] is SatsangStatus.LIVE) == 1


def test_a_satsang_more_than_an_hour_early_is_refused_but_a_late_one_is_not(client) -> None:
    early = _schedule(client, minutes=180)
    assert _publish(client, early.stream_key).status_code == 409
    late = _schedule(client, minutes=-240)  # four hours behind: still this satsang
    assert _publish(client, late.stream_key).status_code == 200


def test_the_speaker_stopping_ends_it_and_frees_the_floor(client) -> None:
    first = _schedule(client, title="First")
    _publish(client, first.stream_key)
    ended = SatsangSessionOut.model_validate(
        client.post("/sessions/ingest/done", json={"stream_key": first.stream_key}).json()
    )
    assert ended.status is SatsangStatus.ENDED and ended.ended_at is not None
    second = _schedule(client, title="Second")
    assert _publish(client, second.stream_key).status_code == 200


def test_a_live_satsang_cannot_be_cancelled_and_an_ended_one_needs_no_key(client) -> None:
    made = _schedule(client)
    _publish(client, made.stream_key)
    assert client.post(f"/sessions/{made.session.id}/cancel", headers=MOD).status_code == 409
    client.post("/sessions/ingest/done", json={"stream_key": made.stream_key})
    assert client.post(f"/sessions/{made.session.id}/key", headers=MOD).status_code == 409


# --- what each audience is shown ----------------------------------------------------------------


def test_an_elder_sees_what_is_coming_what_is_on_and_what_was_called_off(client) -> None:
    coming = _schedule(client, minutes=120, title="Coming")
    called_off = _schedule(client, minutes=90, title="Called off")
    client.post(f"/sessions/{called_off.session.id}/cancel", headers=MOD)
    done = _schedule(client, minutes=-5, title="Already finished")
    _publish(client, done.stream_key)
    client.post("/sessions/ingest/done", json={"stream_key": done.stream_key})

    titles = [i["title"] for i in client.get("/sessions", headers=ELDER).json()["items"]]
    assert "Already finished" not in titles  # the archive is a separate feature
    assert titles == ["Called off", "Coming"]  # soonest first
    assert coming.session.id


def test_the_console_sees_every_status_and_a_stranger_cannot(client) -> None:
    made = _schedule(client)
    client.post(f"/sessions/{made.session.id}/cancel", headers=MOD)
    console = SatsangSessionAdminList.model_validate(
        client.get("/sessions/manage", headers=ADMIN).json()
    )
    assert [s.status for s in console.items] == [SatsangStatus.CANCELLED]
    assert client.get("/sessions/manage", headers=ELDER).status_code == 403


def test_a_session_that_does_not_exist_is_404(client) -> None:
    assert client.post("/sessions/nope/cancel", headers=MOD).status_code == 404
    assert client.post("/sessions/nope/key", headers=MOD).status_code == 404
