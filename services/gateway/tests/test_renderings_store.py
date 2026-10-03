"""Week 7 Phase 4: where the pipeline's per-message output lives.

Four things come out of the pipeline for a message and each needs a home that
survives a restart (the orchestrator resumes from stored stages, Phase 5):
the transcript (voice only), the English pivot, one rendering per language,
and the moderation events (Phase 3). This covers the first three.

Two rules shape everything here:

- Renderings are FIXED AT DELIVERY (M1's answer on #83). Output can only be
  written while the message is still `pending`; once it has gone out, what the
  receivers saw must not change underneath them. Enforced in the write, not
  by hoping the orchestrator is early.
- Writes are idempotent. A retried job writes the same stage again; that must
  replace, never duplicate or fail.

Written before the tables, columns or functions exist.
"""

import uuid

import pytest
from contracts.chat.renderings import Rendering
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import Message, MessageRendering
from app.db.models import User as DbUser
from app.db.renderings import (
    list_renderings,
    rendering_to_out,
    set_message_pivot_text,
    set_message_transcript,
    upsert_rendering,
)
from app.db.repository import create_media_object, create_message, set_message_status


def _user(db_session, name="Alice"):
    user = DbUser(name=name, preferred_language="te", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _message(db_session, *, kind="voice", status="pending"):
    author = _user(db_session, "Author")
    target = _user(db_session, "Target")
    media_kwargs = {}
    if kind == "voice":
        media, _ = create_media_object(
            db_session,
            media_id=uuid.uuid4(),
            author_id=author.id,
            format="webm_opus",
            sha256_hex=uuid.uuid4().hex,
            size_bytes=10,
            duration_ms=4200,
        )
        media_kwargs = {
            "original_media_ref": f"media:{media.id}",
            "media_object_id": media.id,
            "media_format": "webm_opus",
            "media_duration_ms": 4200,
        }
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind=kind,
        text="hello" if kind == "text" else None,
        client_msg_id=uuid.uuid4(),
        **media_kwargs,
    )
    db_session.commit()
    message_id = message.id
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return message_id, author.id


def _audio(db_session, author_id, *, duration_ms=3100):
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author_id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=duration_ms,
    )
    db_session.commit()
    return media.id


# --- renderings ---------------------------------------------------------------------


def test_a_rendering_is_stored_with_its_audio_and_model_versions(db_session):
    message_id, author_id = _message(db_session)
    audio_id = _audio(db_session, author_id)

    assert upsert_rendering(
        db_session,
        message_id=message_id,
        language="hi",
        text="आज सत्संग कब होगा?",
        audio_media_object_id=audio_id,
        model_version_translate="indictrans2",
        model_version_tts="piper:hi_IN-rohan-medium",
    )
    db_session.commit()

    (stored,) = list_renderings(db_session, [message_id])[message_id]
    assert (stored.language, stored.text) == ("hi", "आज सत्संग कब होगा?")
    assert stored.audio_media_object_id == audio_id
    assert stored.degraded_reason is None
    assert stored.model_version_tts == "piper:hi_IN-rohan-medium"


def test_writing_the_same_language_again_replaces_it(db_session):
    # A retried job re-writes the stage it already wrote.
    message_id, _ = _message(db_session)
    upsert_rendering(db_session, message_id=message_id, language="hi", text="पहला")
    upsert_rendering(db_session, message_id=message_id, language="hi", text="दूसरा")
    db_session.commit()

    rows = list_renderings(db_session, [message_id])[message_id]
    assert [r.text for r in rows] == ["दूसरा"]


def test_different_languages_coexist_in_a_stable_order(db_session):
    message_id, _ = _message(db_session)
    upsert_rendering(db_session, message_id=message_id, language="te", text="తెలుగు")
    upsert_rendering(db_session, message_id=message_id, language="en", text="English")
    upsert_rendering(db_session, message_id=message_id, language="hi", text="हिन्दी")
    db_session.commit()

    langs = [r.language for r in list_renderings(db_session, [message_id])[message_id]]
    assert langs == ["en", "hi", "te"]


@pytest.mark.parametrize("status", ["sent", "delivered", "cancelled", "held", "blocked", "failed"])
def test_nothing_can_be_written_once_the_message_is_no_longer_pending(db_session, status):
    message_id, _ = _message(db_session, status=status)

    written = upsert_rendering(db_session, message_id=message_id, language="hi", text="देर से")
    db_session.commit()

    assert written is False
    assert list_renderings(db_session, [message_id]) == {}


def test_a_rendering_already_stored_is_not_changed_after_delivery(db_session):
    message_id, _ = _message(db_session)
    upsert_rendering(db_session, message_id=message_id, language="hi", text="जो भेजा गया")
    db_session.commit()
    set_message_status(db_session, message_id, new_status="sent", expected="pending")
    db_session.commit()

    assert not upsert_rendering(db_session, message_id=message_id, language="hi", text="बदला")
    db_session.commit()
    rows = list_renderings(db_session, [message_id])[message_id]
    assert [r.text for r in rows] == ["जो भेजा गया"]


def test_listing_many_messages_is_one_call_and_omits_messages_without_any(db_session):
    first, _ = _message(db_session)
    second, _ = _message(db_session)
    empty, _ = _message(db_session)
    upsert_rendering(db_session, message_id=first, language="hi", text="a")
    upsert_rendering(db_session, message_id=second, language="te", text="b")
    db_session.commit()

    found = list_renderings(db_session, [first, second, empty])

    assert set(found) == {first, second}


# --- what the database refuses ----------------------------------------------------------


def _raw(message_id, **overrides):
    base = {"message_id": message_id, "language": "hi", "text": "x"}
    base.update(overrides)
    return MessageRendering(**base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": ""},
        {"language": "hi-IN"},
        {"language": "HI"},
        {"language": ""},
        {"degraded_reason": "none"},
        {"degraded_reason": "bogus"},
    ],
    ids=lambda o: ",".join(f"{k}={v!r}" for k, v in o.items()),
)
def test_the_database_refuses_a_rendering_the_contract_would_refuse(db_session, overrides):
    message_id, _ = _message(db_session)
    db_session.add(_raw(message_id, **overrides))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize("reason", ["text_only", "tts_skipped"])
def test_a_no_audio_reason_cannot_carry_audio(db_session, reason):
    message_id, author_id = _message(db_session)
    audio_id = _audio(db_session, author_id)
    db_session.add(_raw(message_id, degraded_reason=reason, audio_media_object_id=audio_id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_swept_audio_leaves_the_rendering_with_its_text(db_session):
    """Retention deletes the media row after 30 days; the rendering's words
    must survive (FK SET NULL), exactly as messages.media_object_id does."""
    message_id, author_id = _message(db_session)
    audio_id = _audio(db_session, author_id)
    upsert_rendering(
        db_session,
        message_id=message_id,
        language="hi",
        text="बचा रहे",
        audio_media_object_id=audio_id,
    )
    db_session.commit()

    db_session.execute(text("DELETE FROM media_objects WHERE id = :id"), {"id": audio_id})
    db_session.commit()
    db_session.expire_all()

    (stored,) = list_renderings(db_session, [message_id])[message_id]
    assert stored.text == "बचा रहे" and stored.audio_media_object_id is None


def test_renderings_go_with_their_message(db_session):
    message_id, _ = _message(db_session, kind="text")
    upsert_rendering(db_session, message_id=message_id, language="hi", text="x")
    db_session.commit()

    db_session.execute(text("DELETE FROM messages WHERE id = :id"), {"id": message_id})
    db_session.commit()

    assert db_session.query(MessageRendering).count() == 0


# --- onto the wire shape ---------------------------------------------------------------------


def test_a_rendering_maps_to_the_contract_with_an_audio_ref_built_from_the_media_row(db_session):
    message_id, author_id = _message(db_session)
    audio_id = _audio(db_session, author_id, duration_ms=3100)
    upsert_rendering(
        db_session,
        message_id=message_id,
        language="hi",
        text="नमस्ते",
        audio_media_object_id=audio_id,
    )
    db_session.commit()

    (stored,) = list_renderings(db_session, [message_id])[message_id]
    out = rendering_to_out(db_session, stored)

    assert isinstance(out, Rendering)
    assert out.language == "hi" and out.degraded_reason is None
    assert out.audio.uri == f"media:{audio_id}"
    assert out.audio.format.value == "wav_pcm16"
    assert out.audio.duration_ms == 3100


def test_a_text_only_rendering_maps_with_its_reason_and_no_audio(db_session):
    message_id, _ = _message(db_session)
    upsert_rendering(
        db_session,
        message_id=message_id,
        language="en",
        text="Hello",
        degraded_reason="text_only",
    )
    db_session.commit()

    (stored,) = list_renderings(db_session, [message_id])[message_id]
    out = rendering_to_out(db_session, stored)

    assert out.audio is None and out.degraded_reason.value == "text_only"


# --- transcript and pivot -----------------------------------------------------------------


def test_a_transcript_is_stored_for_a_pending_voice_note(db_session):
    message_id, _ = _message(db_session)
    assert set_message_transcript(db_session, message_id, "ఈ రోజు సత్సంగం?", "te")
    db_session.commit()
    db_session.expire_all()
    stored = db_session.get(Message, message_id)
    assert (stored.transcript, stored.transcript_language) == ("ఈ రోజు సత్సంగం?", "te")


def test_setting_the_transcript_again_replaces_it(db_session):
    message_id, _ = _message(db_session)
    set_message_transcript(db_session, message_id, "first", "te")
    set_message_transcript(db_session, message_id, "second", "te")
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Message, message_id).transcript == "second"


def test_a_transcript_cannot_be_changed_after_delivery(db_session):
    message_id, _ = _message(db_session, status="sent")
    assert set_message_transcript(db_session, message_id, "late", "te") is False
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Message, message_id).transcript is None


def test_the_database_refuses_a_transcript_on_a_text_message(db_session):
    message_id, _ = _message(db_session, kind="text")
    with pytest.raises(IntegrityError):
        set_message_transcript(db_session, message_id, "nope", "en")
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE messages SET transcript = 'x' WHERE id = :id",  # no language
        "UPDATE messages SET transcript_language = 'te' WHERE id = :id",  # no text
        "UPDATE messages SET transcript = '', transcript_language = 'te' WHERE id = :id",
        "UPDATE messages SET transcript = 'x', transcript_language = 'te-IN' WHERE id = :id",
    ],
)
def test_the_database_refuses_an_inconsistent_transcript_pair(db_session, sql):
    message_id, _ = _message(db_session)
    with pytest.raises(IntegrityError):
        db_session.execute(text(sql), {"id": message_id})
    db_session.rollback()


def test_the_english_pivot_is_stored_while_pending_and_fixed_afterwards(db_session):
    message_id, _ = _message(db_session)
    assert set_message_pivot_text(db_session, message_id, "When is satsang?")
    db_session.commit()
    set_message_status(db_session, message_id, new_status="sent", expected="pending")
    db_session.commit()

    assert set_message_pivot_text(db_session, message_id, "changed") is False
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Message, message_id).pivot_text_en == "When is satsang?"
