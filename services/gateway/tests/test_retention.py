"""Tests for the retention sweeper (app/retention.py, Week 6 Step 1).

Every test ages an artifact by inserting it with an explicit past
created_at, never by waiting real days for one to expire. Every assertion
checks the actual, observable result -- the row is gone from the
database, the bytes are gone from storage -- never that some function was
merely called.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.db.models import MediaObject
from app.db.models import User as DbUser
from app.media_storage import get_media_storage
from app.retention import sweep_expired_media


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _make_aged_media(db_session, *, author_id, age_days, sha256_hex=None):
    media_id = uuid.uuid4()
    media = MediaObject(
        id=media_id,
        author_id=author_id,
        format="wav_pcm16",
        sha256_hex=sha256_hex or uuid.uuid4().hex,
        size_bytes=1024,
        duration_ms=3000,
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db_session.add(media)
    db_session.flush()
    get_media_storage().put(str(media_id), b"pretend-audio-bytes")
    return media


def test_sweep_deletes_row_and_bytes_for_an_expired_artifact(db_session):
    alice = _make_db_user(db_session, "Alice")
    old = _make_aged_media(db_session, author_id=alice.id, age_days=31)
    db_session.commit()

    swept = sweep_expired_media(retention_days=30)

    assert str(old.id) in swept
    assert db_session.get(MediaObject, old.id) is None, "row must actually be gone"
    assert get_media_storage().get(str(old.id)) is None, "bytes must actually be gone"


def test_sweep_leaves_a_fresh_artifact_untouched(db_session):
    alice = _make_db_user(db_session, "Alice")
    fresh = _make_aged_media(db_session, author_id=alice.id, age_days=5)
    db_session.commit()

    swept = sweep_expired_media(retention_days=30)

    assert str(fresh.id) not in swept
    assert db_session.get(MediaObject, fresh.id) is not None
    assert get_media_storage().get(str(fresh.id)) == b"pretend-audio-bytes"


def test_sweep_leaves_an_artifact_exactly_at_the_boundary_untouched(db_session):
    # created_at < cutoff is the sweep condition (strictly before) -- an
    # artifact whose age exactly equals the retention window has not yet
    # crossed it and must survive this run.
    alice = _make_db_user(db_session, "Alice")
    boundary = _make_aged_media(db_session, author_id=alice.id, age_days=30)
    db_session.commit()

    swept = sweep_expired_media(retention_days=30, now=boundary.created_at + timedelta(days=30))

    assert str(boundary.id) not in swept
    assert db_session.get(MediaObject, boundary.id) is not None


def test_sweep_window_is_configurable_not_a_fixed_30_days(db_session):
    alice = _make_db_user(db_session, "Alice")
    ten_days_old = _make_aged_media(db_session, author_id=alice.id, age_days=10)
    db_session.commit()

    # Same artifact, same age -- a 7-day window sweeps it, a 30-day window
    # (the default) would not have. Proves the window is a real parameter,
    # not a hardcoded number reading as configurable.
    swept = sweep_expired_media(retention_days=7)

    assert str(ten_days_old.id) in swept
    assert db_session.get(MediaObject, ten_days_old.id) is None


def test_sweep_only_touches_expired_rows_when_several_exist(db_session):
    alice = _make_db_user(db_session, "Alice")
    old = _make_aged_media(db_session, author_id=alice.id, age_days=45)
    fresh = _make_aged_media(db_session, author_id=alice.id, age_days=1)
    db_session.commit()

    swept = sweep_expired_media(retention_days=30)

    assert swept == [str(old.id)]
    assert db_session.get(MediaObject, fresh.id) is not None


def test_swept_media_404s_on_fetch_same_as_an_id_that_never_existed(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    audio = b"pretend-this-is-real-audio-bytes"
    upload = client.post("/media", params={"format": "wav_pcm16"}, content=audio)
    assert upload.status_code == 200
    media_id = upload.json()["uri"].removeprefix("media:")

    # Backdate it directly -- there is no API for this, deliberately: a
    # client never gets to choose when its own upload expires.
    db_session.execute(
        MediaObject.__table__.update()
        .where(MediaObject.id == uuid.UUID(media_id))
        .values(created_at=datetime.now(UTC) - timedelta(days=31))
    )
    db_session.commit()

    sweep_expired_media(retention_days=30)

    fetched = client.get(f"/media/{media_id}")
    assert fetched.status_code == 404
    assert "detail" in fetched.json()

    unknown = client.get(f"/media/{uuid.uuid4()}")
    assert unknown.status_code == 404
    # Same response shape as a swept id -- once the row is gone there is
    # nothing left server-side to tell the two cases apart. See
    # app/retention.py's module docstring for why that's a direct
    # consequence of "the row is actually deleted," not a separate choice.
    assert fetched.json().keys() == unknown.json().keys()
