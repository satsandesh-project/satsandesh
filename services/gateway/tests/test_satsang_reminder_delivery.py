"""The satsang reminder, delivered: in each member's own language, with audio (app/satsang_reminder.py).

The clock (when the reminder fires, once, not early) is tests/test_satsang_reminder.py. This file is what
happens when it fires:

- the body is written ONCE, in English, and rendered ONCE per distinct language the recipients read
  (one render call for K languages, never one per member), through the real AiClient against the
  scripted FakeAi harness (tests/_fake_ai.py), which speaks the real contracts;
- the render call is made with NO database transaction open (Postgres kills a connection that sits
  idle in a transaction for 30 s, and the render service is slow), and the satsang row is locked again
  afterwards, so a second worker still cannot send twice;
- a reminder nobody receives is worse than one in the wrong language: if the render service is down the
  reminder goes out in the source language, marked degraded; if only speech is down it is text only;
- every push goes through app/push.py's send_push, so one dead device never blocks another;
- quiet hours: send anyway, unless the user EXPLICITLY set their own window (decision in the module
  docstring: an app default must not silence an early-morning satsang).

Time is injected (see test_satsang_reminder.py); nothing sleeps.
"""

import uuid
import wave
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from contracts.chat.satsang import SatsangSessionCreated
from pywebpush import WebPushException
from sqlalchemy import select, text

import app.push as push_module
import app.satsang_reminder as reminder
from app.config import get_settings
from app.db import repository
from app.db.models import MediaObject, PushSubscription
from app.db.models import User as DbUser
from app.db.satsang import SatsangReminderRendering, SatsangSession
from app.media_storage import get_media_storage
from app.tokens import issue_token
from tests._fake_ai import FakeAi

LEAD = timedelta(minutes=30)
SOURCE = "A discourse begins in 30 minutes."


class Clock:
    def __init__(self, monkeypatch) -> None:
        self.current = datetime.now(UTC)
        outer = self

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return outer.current if tz is None else outer.current.astimezone(tz)

        monkeypatch.setattr(repository, "datetime", Frozen)
        monkeypatch.setattr(reminder, "_now", lambda: outer.current)

    def at(self, when: datetime) -> None:
        self.current = when


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """The AI services, scripted. Each language's audio file holds different samples, so a test can
    tell the Telugu recording from the Hindi one."""
    ai = FakeAi(audio_dir=tmp_path)
    ai.tts_languages = {"en", "hi", "te"}
    original = ai._spoken
    fill = {"te": b"\x02\x00", "hi": b"\x03\x00", "en": b"\x04\x00"}

    def spoken(pivot_text, language):
        result = original(pivot_text, language)
        if ai.write_audio_files:
            name = result.audio.uri.rsplit("/", 1)[-1]
            with wave.open(str(tmp_path / name), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(fill.get(language, b"\0") * 800)
        return result

    ai._spoken = spoken
    monkeypatch.setattr(reminder, "_ai_client", lambda: ai.client())
    monkeypatch.setattr(get_settings(), "AI_RENDER_AUDIO_ROOT", str(tmp_path))
    return ai


@pytest.fixture
def pushes(monkeypatch):
    calls: list[tuple[uuid.UUID, str, str]] = []
    monkeypatch.setattr(
        reminder,
        "send_push",
        lambda session, *, user_id, title, body: calls.append((user_id, title, body)),
    )
    return calls


def _user(db_session, name, language="te", role="elder", **extra):
    user = DbUser(name=name, preferred_language=language, role=role, **extra)
    db_session.add(user)
    db_session.commit()
    return user


def _subscribe(db_session, user, n=1):
    endpoints = []
    for _ in range(n):
        endpoint = f"https://push.example/{uuid.uuid4()}"
        endpoints.append(endpoint)
        db_session.add(PushSubscription(user_id=user.id, endpoint=endpoint, p256dh="k", auth="a"))
    db_session.commit()
    return endpoints


def _member(db_session, name, language="te", subscribed=True, **extra):
    user = _user(db_session, name, language, **extra)
    if subscribed:
        _subscribe(db_session, user)
    return user


@pytest.fixture
def admin(db_session):
    return _user(db_session, "Asha", "te", "admin")


@pytest.fixture
def swami(db_session):
    return _user(db_session, "Swami Ananda")


def _headers(user):
    return {"Authorization": f"Bearer {issue_token(user.id)}"}


def _schedule(client, who, speaker, *, minutes=120, title="Thursday discourse"):
    resp = client.post(
        "/sessions",
        json={
            "title": title,
            "speaker_user_id": str(speaker.id),
            "scheduled_start_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
        },
        headers=_headers(who),
    )
    assert resp.status_code == 201, resp.text
    return SatsangSessionCreated.model_validate(resp.json())


def _row(db_session, session_id) -> SatsangSession:
    db_session.expire_all()
    return db_session.get(SatsangSession, uuid.UUID(session_id))


def _fire(client, db_session, admin, swami, clock, **kw):
    """Schedule a satsang, move the clock to its reminder time and run the handler once."""
    made = _schedule(client, admin, swami, **kw)
    row = _row(db_session, made.session.id)
    clock.at(row.scheduled_start_at - LEAD)
    run(db_session, made.session.id)
    return made, row


def run(db_session, session_id):
    reminder.handle_satsang_reminder(db_session, {"session_id": str(session_id)})
    db_session.commit()


def _renderings(db_session, session_id):
    db_session.expire_all()
    rows = db_session.scalars(
        select(SatsangReminderRendering).where(
            SatsangReminderRendering.session_id == uuid.UUID(session_id)
        )
    )
    return {r.language: r for r in rows}


# --- one body, rendered once per language, with audio ------------------------------------------------


def test_a_telugu_and_a_hindi_member_get_the_same_reminder_in_their_own_languages_with_audio(
    client, db_session, admin, swami, clock, fake, pushes
):
    telugu = _member(db_session, "Te", "te")
    hindi = _member(db_session, "Hi", "hi")
    made, _ = _fire(client, db_session, admin, swami, clock, title="Gita discourse")

    by_user = {uid: (title, body) for uid, title, body in pushes}
    assert by_user[telugu.id] == ("Gita discourse", f"[te] {SOURCE}")
    assert by_user[hindi.id] == ("Gita discourse", f"[hi] {SOURCE}")

    te = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(telugu)).json()
    hi = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(hindi)).json()
    assert (te["language"], te["text"], te["translated"]) == ("te", f"[te] {SOURCE}", True)
    assert (hi["language"], hi["text"], hi["translated"]) == ("hi", f"[hi] {SOURCE}", True)
    assert te["audio_url"] and hi["audio_url"] and te["degraded_reason"] is None

    te_audio = client.get(te["audio_url"], headers=_headers(telugu))
    hi_audio = client.get(hi["audio_url"], headers=_headers(hindi))
    assert te_audio.status_code == hi_audio.status_code == 200
    assert te_audio.headers["content-type"] == "audio/wav"
    assert te_audio.content[:4] == b"RIFF" and hi_audio.content[:4] == b"RIFF"
    assert te_audio.content != hi_audio.content  # a different recording per language


def test_the_translation_and_the_speech_are_asked_for_once_however_many_members_there_are(
    client, db_session, admin, swami, clock, fake, pushes
):
    for i in range(3):
        _member(db_session, f"Te{i}", "te")
    for i in range(2):
        _member(db_session, f"Hi{i}", "hi")
    _fire(client, db_session, admin, swami, clock)

    (call,) = fake.requests("render")  # ONE call, not one per member and not one per language
    assert sorted(call["target_languages"]) == ["hi", "te"]
    assert call["pivot_text"] == SOURCE
    assert len(pushes) == 5  # every member is still pushed


def test_only_languages_somebody_subscribed_to_push_reads_are_rendered(
    client, db_session, admin, swami, clock, fake, pushes
):
    _member(db_session, "Te", "te")
    _member(db_session, "Hi but not subscribed", "hi", subscribed=False)
    _fire(client, db_session, admin, swami, clock)
    assert fake.requests("render")[0]["target_languages"] == ["te"]


def test_the_title_is_never_machine_translated_and_the_word_satsang_is_avoided(
    client, db_session, admin, swami, clock, fake, pushes
):
    """The Week 8 comparison measured the MT turning 'satsang' into 'chess' (शतरंज) in Hindi. The title
    travels as the push title, untouched; the translated sentence does not contain the word."""
    _member(db_session, "Hi", "hi")
    _fire(client, db_session, admin, swami, clock, title="Satsang on patience")
    pivot = fake.requests("render")[0]["pivot_text"]
    assert "satsang" not in pivot.lower() and "patience" not in pivot.lower()
    ((_, title, _),) = pushes
    assert title == "Satsang on patience"


def test_a_reminder_with_one_minute_left_says_minute_not_minutes(
    client, db_session, admin, swami, clock, fake, pushes
):
    _member(db_session, "En", "en")
    made = _schedule(client, admin, swami)
    row = _row(db_session, made.session.id)
    clock.at(row.scheduled_start_at - timedelta(seconds=30))
    run(db_session, made.session.id)
    assert fake.requests("render")[0]["pivot_text"] == "A discourse begins in 1 minute."


def test_the_renderings_are_stored_once_per_language_with_their_audio(
    client, db_session, admin, swami, clock, fake, pushes
):
    _member(db_session, "Te", "te")
    _member(db_session, "Hi", "hi")
    made, _ = _fire(client, db_session, admin, swami, clock)
    rows = _renderings(db_session, made.session.id)
    # the English source row is always stored, for any reader whose language was not rendered
    assert sorted(rows) == ["en", "hi", "te"]
    assert (rows["en"].text, rows["en"].translated, rows["en"].audio_media_object_id) == (
        SOURCE,
        True,
        None,
    )
    for language in ("hi", "te"):
        rendering = rows[language]
        assert rendering.text == f"[{language}] {SOURCE}" and rendering.translated is True
        media = db_session.get(MediaObject, rendering.audio_media_object_id)
        assert media is not None and media.format == "wav_pcm16"
        assert get_media_storage().get(str(media.id))[:4] == b"RIFF"
        assert media.author_id == admin.id  # owned by whoever scheduled it


# --- TTS off, MT down, TTS down -------------------------------------------------------------------------


def test_a_member_with_speech_switched_off_gets_text_and_no_audio_not_a_failure(
    client, db_session, admin, swami, clock, fake, pushes
):
    quiet = _member(db_session, "NoAudio", "te", tts_on=False)
    loud = _member(db_session, "Audio", "te", tts_on=True)
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert {uid for uid, _, _ in pushes} == {quiet.id, loud.id}  # both are told

    off = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(quiet))
    assert off.status_code == 200 and off.json()["audio_url"] is None
    assert off.json()["text"] == f"[te] {SOURCE}"
    assert (
        client.get(
            f"/sessions/{made.session.id}/reminder/audio", headers=_headers(quiet)
        ).status_code
        == 404
    )
    on = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(loud)).json()
    assert on["audio_url"]


def test_with_the_render_service_down_the_reminder_still_goes_out_in_the_source_language(
    client, db_session, admin, swami, clock, fake, pushes
):
    """A reminder nobody receives is worse than one in the wrong language."""
    fake.errors["render"] = httpx.ConnectError("render down")
    telugu = _member(db_session, "Te", "te")
    hindi = _member(db_session, "Hi", "hi")
    made, _ = _fire(client, db_session, admin, swami, clock)

    assert {(uid, body) for uid, _, body in pushes} == {(telugu.id, SOURCE), (hindi.id, SOURCE)}
    assert (
        _row(db_session, made.session.id).reminder_sent_at is not None
    )  # sent: not retried forever
    seen = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(telugu)).json()
    assert (seen["language"], seen["text"], seen["translated"]) == ("en", SOURCE, False)
    assert seen["degraded_reason"] == "text_only" and seen["audio_url"] is None


def test_with_speech_down_the_reminder_is_translated_text_without_audio(
    client, db_session, admin, swami, clock, fake, pushes
):
    original = fake._render

    def translate_without_a_voice(body):
        """A render service whose voice is down still translates: only the audio is skipped."""
        out = original(body)
        for result in out["results"]:
            result["text"] = f"[{result['language']}] {body['pivot_text']}"
        return out

    fake.tts_languages = set()
    fake._render = translate_without_a_voice
    telugu = _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert [body for _, _, body in pushes] == [f"[te] {SOURCE}"]
    seen = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(telugu)).json()
    assert (seen["translated"], seen["degraded_reason"], seen["audio_url"]) == (
        True,
        "text_only",
        None,
    )


def test_a_language_with_no_voice_is_text_only_not_a_failure(
    client, db_session, admin, swami, clock, fake, pushes
):
    """The real render service has no English voice: asked for `en` it answers text_only. Measured on
    the server on 14 Oct; the scripted fake otherwise speaks every language, so this pins it."""
    fake.tts_languages = {"hi", "te"}
    english = _member(db_session, "En", "en")
    telugu = _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert {uid for uid, _, _ in pushes} == {english.id, telugu.id}  # both are told
    seen = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(english)).json()
    assert (seen["language"], seen["degraded_reason"], seen["audio_url"]) == (
        "en",
        "text_only",
        None,
    )
    assert client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(telugu)).json()[
        "audio_url"
    ]


def test_an_audio_file_that_cannot_be_read_costs_the_audio_not_the_reminder(
    client, db_session, admin, swami, clock, fake, pushes
):
    fake.write_audio_files = False
    telugu = _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert len(pushes) == 1
    seen = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(telugu)).json()
    assert (seen["text"], seen["degraded_reason"], seen["audio_url"]) == (
        f"[te] {SOURCE}",
        "tts_skipped",
        None,
    )


def test_a_language_the_pipeline_cannot_render_reads_the_source_text(
    client, db_session, admin, swami, clock, fake, pushes
):
    tamil = _member(db_session, "Ta", "ta")
    telugu = _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert fake.requests("render")[0]["target_languages"] == ["te"]  # 'ta' is not asked for
    bodies = {uid: body for uid, _, body in pushes}
    assert bodies[tamil.id] == SOURCE and bodies[telugu.id] == f"[te] {SOURCE}"
    seen = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(tamil)).json()
    assert (seen["language"], seen["translated"]) == ("en", False)


def test_an_empty_translation_for_one_language_does_not_cost_the_others_theirs(
    client, db_session, admin, swami, clock, fake, pushes, monkeypatch
):
    original = fake._render

    def render(body):
        out = original(body)
        for result in out["results"]:
            if result["language"] == "hi":
                result["text"] = "   "
        return out

    fake._render = render
    telugu = _member(db_session, "Te", "te")
    hindi = _member(db_session, "Hi", "hi")
    _fire(client, db_session, admin, swami, clock)
    bodies = {uid: body for uid, _, body in pushes}
    assert bodies[telugu.id] == f"[te] {SOURCE}" and bodies[hindi.id] == SOURCE


# --- the shape of the work: no transaction open during the call, sent once -----------------------------


def test_the_render_call_is_made_with_no_database_transaction_open(
    client, db_session, admin, swami, clock, fake, pushes
):
    """Postgres kills a connection that is idle in a transaction for 30 s (app/db/base.py) and the real
    render service takes seconds per language: holding one open across the call would fail the job."""
    seen = []
    fake.on_call = lambda stage: seen.append((stage, db_session.in_transaction()))
    _member(db_session, "Te", "te")
    _fire(client, db_session, admin, swami, clock)
    assert seen == [("render", False)]


def test_a_second_run_neither_renders_nor_pushes_again(
    client, db_session, admin, swami, clock, fake, pushes
):
    _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    run(db_session, made.session.id)
    run(db_session, made.session.id)
    assert len(fake.requests("render")) == 1 and len(pushes) == 1


def test_a_satsang_cancelled_while_the_translation_was_being_made_sends_nothing(
    client, db_session, admin, swami, clock, fake, pushes
):
    """The row is read, the transaction ended, the service called, and the row locked and read AGAIN:
    what was true before the slow call is not trusted after it."""
    _member(db_session, "Te", "te")
    made = _schedule(client, admin, swami)
    row = _row(db_session, made.session.id)
    clock.at(row.scheduled_start_at - LEAD)

    def cancel_meanwhile(stage):
        db_session.execute(
            text("update sessions set status='cancelled' where id=:i"), {"i": made.session.id}
        )
        db_session.commit()

    fake.on_call = cancel_meanwhile
    run(db_session, made.session.id)
    assert pushes == []
    assert _row(db_session, made.session.id).reminder_sent_at is None


def test_a_push_to_nobody_makes_no_render_call_at_all(
    client, db_session, admin, swami, clock, fake
):
    _member(db_session, "Nobody subscribed", "te", subscribed=False)
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert fake.calls == []
    assert _row(db_session, made.session.id).reminder_sent_at is not None


# --- push failures stay local (app/push.py's rule, not regressed) --------------------------------------


def test_one_dead_device_does_not_block_the_users_other_devices_or_other_users(
    client, db_session, admin, swami, clock, fake, monkeypatch
):
    attempted: list[str] = []
    dead: set[str] = set()

    def fake_webpush(*, subscription_info, data, vapid_private_key, vapid_claims, timeout=None):
        endpoint = subscription_info["endpoint"]
        attempted.append(endpoint)
        if endpoint in dead:
            raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(push_module, "webpush", fake_webpush)
    first = _member(db_session, "First", "te", subscribed=False)
    second = _member(db_session, "Second", "hi", subscribed=False)
    first_devices = _subscribe(db_session, first, n=2)
    second_devices = _subscribe(db_session, second)
    dead.add(first_devices[0])  # the push service says this endpoint is gone for good

    _fire(client, db_session, admin, swami, clock)

    assert sorted(attempted) == sorted([*first_devices, *second_devices])  # everyone was tried
    db_session.expire_all()
    left = {s.endpoint for s in db_session.scalars(select(PushSubscription))}
    assert first_devices[0] not in left  # a 410 removes the dead subscription (push.py's rule)
    assert {first_devices[1], *second_devices} <= left


def test_a_push_that_fails_for_another_reason_is_skipped_not_raised(
    client, db_session, admin, swami, clock, fake, monkeypatch
):
    def flaky(*, subscription_info, data, vapid_private_key, vapid_claims):
        raise WebPushException("overloaded", response=SimpleNamespace(status_code=503))

    monkeypatch.setattr(push_module, "webpush", flaky)
    _member(db_session, "First", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert _row(db_session, made.session.id).reminder_sent_at is not None  # the reminder was sent


# --- quiet hours: send anyway, unless the user EXPLICITLY chose a window ---------------------------------


def _early_morning(db_session, made):
    """Move the satsang to 05:00 UTC tomorrow: its reminder is at 04:30, inside the default 20:00-07:00."""
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=5, minute=0, second=0, microsecond=0
    )
    db_session.execute(
        text("update sessions set scheduled_start_at=:s where id=:i"),
        {"s": start, "i": made.session.id},
    )
    db_session.commit()
    return start


def _fire_early(client, db_session, admin, swami, clock):
    made = _schedule(client, admin, swami)
    start = _early_morning(db_session, made)
    clock.at(start - LEAD)
    run(db_session, made.session.id)
    return made


def test_the_default_quiet_hours_do_not_silence_an_early_morning_satsang_reminder(
    client, db_session, admin, swami, clock, fake, pushes
):
    """The user has a timezone but never picked a window, so the app's 20:00-07:00 default is what
    would apply: and a reminder at 04:30 for a 05:00 satsang is the whole point of the feature."""
    sleeper = _member(db_session, "Default window", "te", timezone="UTC")
    _fire_early(client, db_session, admin, swami, clock)
    assert [uid for uid, _, _ in pushes] == [sleeper.id]


def test_a_window_the_user_chose_themselves_is_honoured(
    client, db_session, admin, swami, clock, fake, pushes
):
    from datetime import time

    chosen = _member(
        db_session,
        "Chose a window",
        "te",
        timezone="UTC",
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(6, 0),
    )
    other = _member(db_session, "Default window", "te", timezone="UTC")
    _fire_early(client, db_session, admin, swami, clock)
    assert [uid for uid, _, _ in pushes] == [other.id]
    assert chosen.id not in {uid for uid, _, _ in pushes}


def test_a_window_with_only_one_end_chosen_counts_as_chosen(
    client, db_session, admin, swami, clock, fake, pushes
):
    from datetime import time

    half = _member(db_session, "Half a window", "te", timezone="UTC", quiet_hours_start=time(22, 0))
    _fire_early(client, db_session, admin, swami, clock)
    assert half.id not in {uid for uid, _, _ in pushes}


def test_a_user_outside_their_chosen_window_is_pushed(
    client, db_session, admin, swami, clock, fake, pushes
):
    from datetime import time

    afternoon = _member(
        db_session,
        "Afternoon window",
        "te",
        timezone="UTC",
        quiet_hours_start=time(13, 0),
        quiet_hours_end=time(15, 0),
    )
    _fire_early(client, db_session, admin, swami, clock)
    assert [uid for uid, _, _ in pushes] == [afternoon.id]


# --- reading a reminder -----------------------------------------------------------------------------------


def test_reading_a_reminder_needs_a_signed_in_user(
    client, db_session, admin, swami, clock, fake, pushes
):
    made, _ = _fire(client, db_session, admin, swami, clock)
    assert client.get(f"/sessions/{made.session.id}/reminder").status_code == 401
    assert client.get(f"/sessions/{made.session.id}/reminder/audio").status_code == 401


def test_there_is_no_reminder_before_it_has_been_sent(client, db_session, admin, swami):
    made = _schedule(client, admin, swami)
    reader = _user(db_session, "Reader")
    assert (
        client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(reader)).status_code
        == 404
    )
    assert (
        client.get(
            f"/sessions/{made.session.id}/reminder/audio", headers=_headers(reader)
        ).status_code
        == 404
    )


@pytest.mark.parametrize("bad", ["not-a-uuid", str(uuid.uuid4())])
def test_an_unknown_satsang_has_no_reminder(client, db_session, bad):
    reader = _user(db_session, "Reader")
    assert client.get(f"/sessions/{bad}/reminder", headers=_headers(reader)).status_code == 404


def test_a_reader_whose_language_was_not_rendered_reads_the_source_text(
    client, db_session, admin, swami, clock, fake, pushes
):
    _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    hindi_reader = _user(db_session, "Hindi reader not subscribed", "hi")
    seen = client.get(
        f"/sessions/{made.session.id}/reminder", headers=_headers(hindi_reader)
    ).json()
    assert (seen["language"], seen["text"], seen["translated"]) == ("en", SOURCE, False)
    assert seen["audio_url"] is None


def test_the_reminder_never_carries_a_key_a_user_id_or_bookkeeping(
    client, db_session, admin, swami, clock, fake, pushes
):
    reader = _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    body = client.get(f"/sessions/{made.session.id}/reminder", headers=_headers(reader)).text
    for secret in (str(admin.id), str(swami.id), str(reader.id), "stream_key", "reminder_sent_at"):
        assert secret not in body


# --- the stored rendering ----------------------------------------------------------------------------------


def test_a_rendering_goes_when_its_satsang_goes_and_loses_only_its_audio_when_swept(
    client, db_session, admin, swami, clock, fake, pushes
):
    _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    rendering = _renderings(db_session, made.session.id)["te"]
    media_id = rendering.audio_media_object_id
    # retention sweeps the audio after MEDIA_RETENTION_DAYS: the words outlive it (SET NULL)
    db_session.execute(text("delete from media_objects where id=:i"), {"i": media_id})
    db_session.commit()
    assert _renderings(db_session, made.session.id)["te"].audio_media_object_id is None
    db_session.execute(text("delete from sessions where id=:i"), {"i": made.session.id})
    db_session.commit()
    assert _renderings(db_session, made.session.id) == {}


def test_two_renderings_for_one_language_are_refused_by_the_database(
    client, db_session, admin, swami, clock, fake, pushes
):
    from sqlalchemy.exc import IntegrityError

    _member(db_session, "Te", "te")
    made, _ = _fire(client, db_session, admin, swami, clock)
    db_session.add(
        SatsangReminderRendering(session_id=uuid.UUID(made.session.id), language="te", text="again")
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_an_empty_text_or_a_malformed_language_is_refused_by_the_database(
    client, db_session, admin, swami
):
    from sqlalchemy.exc import IntegrityError

    made = _schedule(client, admin, swami)
    for language, body in (("te", ""), ("TE", "x"), ("telugu", "x")):
        db_session.add(
            SatsangReminderRendering(
                session_id=uuid.UUID(made.session.id), language=language, text=body
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
