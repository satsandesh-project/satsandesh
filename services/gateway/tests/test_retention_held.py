"""OPEN_QUESTIONS #9: the retention sweep must not delete the evidence a moderator is deciding on.

When #9 was written there was no `moderation_events` table and no notion of a message waiting for
a human, so "the sweep could delete audio an appeal needs" was hypothetical. Both exist now. The
decision this file pins down:

  * the audio of a message that is `held` (a human has not yet ruled) is NOT swept, whether it is
    the voice note itself or one of its renderings' audio, however old it is;
  * once the message leaves `held` (released, blocked, cancelled, deleted) the ordinary
    30-day window applies again;
  * `blocked` is NOT protected here. A block is a decision, and whether blocked audio should be
    kept past 30 days for an appeal (Week 9) is a retention/privacy policy question for people, not
    something to decide silently in a query. It is a one-line change (`_UNRESOLVED_STATUSES`).

Every test ages media by inserting it with a past created_at and asserts the observable result: the
row and the bytes are still there, or are gone.

Written before find_expired_media knows about held messages.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import MediaObject
from app.db.models import User as DbUser
from app.db.renderings import upsert_rendering
from app.db.repository import create_message, set_message_status
from app.media_storage import get_media_storage
from app.retention import sweep_expired_media

OLD = 90  # days: far past the 30-day window


def _user(db_session, name="Author"):
    user = DbUser(name=name, preferred_language="te", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _aged_media(db_session, author_id, age_days=OLD):
    media_id = uuid.uuid4()
    media = MediaObject(
        id=media_id,
        author_id=author_id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=1024,
        duration_ms=3000,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db_session.add(media)
    db_session.flush()
    get_media_storage().put(str(media_id), b"pretend-audio-bytes")
    return media_id


def _voice_message(db_session, author, media_id, status):
    target = _user(db_session, "Target")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="voice",
        source_lang="te",
        client_msg_id=uuid.uuid4(),
        original_media_ref=f"media:{media_id}",
        media_object_id=media_id,
        media_format="wav_pcm16",
        media_duration_ms=3000,
        undo_expires_at=datetime.now(UTC) + timedelta(seconds=300),
    )
    db_session.commit()
    if status != "pending":
        assert set_message_status(db_session, message.id, new_status=status, expected="pending")
        db_session.commit()
    return message.id


def _kept(db_session, media_id) -> bool:
    db_session.expire_all()
    return (
        db_session.get(MediaObject, media_id) is not None
        and get_media_storage().get(str(media_id)) is not None
    )


def _gone(db_session, media_id) -> bool:
    db_session.expire_all()
    return (
        db_session.get(MediaObject, media_id) is None
        and get_media_storage().get(str(media_id)) is None
    )


def test_the_voice_note_of_a_held_message_survives_the_sweep(db_session):
    author = _user(db_session)
    media_id = _aged_media(db_session, author.id)
    _voice_message(db_session, author, media_id, "held")

    swept = sweep_expired_media(retention_days=30)

    assert str(media_id) not in swept
    assert _kept(db_session, media_id), "the recording a moderator is judging must still play"


def test_the_audio_of_a_rendering_of_a_held_message_survives_the_sweep(db_session):
    author = _user(db_session)
    original = _aged_media(db_session, author.id, age_days=1)  # young: not what is tested
    rendering_audio = _aged_media(db_session, author.id)  # old
    message_id = _voice_message(db_session, author, original, "pending")
    assert upsert_rendering(
        db_session,
        message_id=message_id,
        language="hi",
        text="नमस्ते",
        audio_media_object_id=rendering_audio,
    )
    db_session.commit()
    assert set_message_status(db_session, message_id, new_status="held", expected="pending")
    db_session.commit()

    swept = sweep_expired_media(retention_days=30)

    assert str(rendering_audio) not in swept
    assert _kept(db_session, rendering_audio)


def test_once_a_held_message_is_released_its_old_audio_is_swept_again(db_session):
    author = _user(db_session)
    media_id = _aged_media(db_session, author.id)
    message_id = _voice_message(db_session, author, media_id, "held")
    sweep_expired_media(retention_days=30)
    assert _kept(db_session, media_id)

    assert set_message_status(db_session, message_id, new_status="sent", expected="held")
    db_session.commit()
    swept = sweep_expired_media(retention_days=30)

    assert str(media_id) in swept
    assert _gone(db_session, media_id), "protection ends when the human has ruled"


@pytest.mark.parametrize("status", ["sent", "delivered", "cancelled", "blocked"])
def test_only_held_is_protected_every_other_status_follows_the_normal_window(db_session, status):
    # `blocked` is here on purpose and is the DECISION recorded in the module docstring: a block
    # is a ruling, and keeping blocked audio for an appeal is a policy call for people (#9, #65).
    author = _user(db_session)
    media_id = _aged_media(db_session, author.id)
    _voice_message(db_session, author, media_id, status)

    swept = sweep_expired_media(retention_days=30)

    assert str(media_id) in swept
    assert _gone(db_session, media_id)


def test_a_young_held_audio_is_obviously_kept_too(db_session):
    author = _user(db_session)
    media_id = _aged_media(db_session, author.id, age_days=2)
    _voice_message(db_session, author, media_id, "held")

    assert sweep_expired_media(retention_days=30) == []
    assert _kept(db_session, media_id)


def test_media_of_a_deleted_held_message_is_not_protected(db_session):
    from sqlalchemy import update

    from app.db.models import Message

    author = _user(db_session)
    media_id = _aged_media(db_session, author.id)
    message_id = _voice_message(db_session, author, media_id, "held")
    db_session.execute(
        update(Message).where(Message.id == message_id).values(deleted_at=datetime.now(UTC))
    )
    db_session.commit()

    assert str(media_id) in sweep_expired_media(retention_days=30)


def test_an_orphan_old_media_with_no_message_is_still_swept(db_session):
    author = _user(db_session)
    media_id = _aged_media(db_session, author.id)
    db_session.commit()

    assert str(media_id) in sweep_expired_media(retention_days=30)
    assert _gone(db_session, media_id)


def test_only_the_protected_one_survives_when_both_are_old(db_session):
    author = _user(db_session)
    protected = _aged_media(db_session, author.id)
    ordinary = _aged_media(db_session, author.id)
    _voice_message(db_session, author, protected, "held")
    _voice_message(db_session, author, ordinary, "sent")

    swept = sweep_expired_media(retention_days=30)

    assert str(protected) not in swept and str(ordinary) in swept
    assert _kept(db_session, protected) and _gone(db_session, ordinary)
