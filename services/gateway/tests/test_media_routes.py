"""HTTP route tests for POST /media and GET /media/{id} — exercised
against the real app.main.app via TestClient, with the get_db dependency
overridden to the test Postgres session and get_current_user swapped per
test via login_as (tests/conftest.py). Same pattern as
tests/test_message_routes.py.

Written before app/media.py exists — collecting this file succeeds
(nothing here imports it directly), but every test is expected to fail
until Step 3's implementation lands: a 404 for the route not existing, or
an ImportError for get_settings().MEDIA_MAX_UPLOAD_BYTES not existing yet.
That failure is the point: these tests fix the route contract the
implementation has to satisfy, not the other way around.
"""

import uuid

from app.config import get_settings
from app.db.models import User as DbUser


def _make_db_user(db_session, name="User", preferred_language="en", role="elder"):
    user = DbUser(name=name, preferred_language=preferred_language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def test_upload_then_fetch_returns_the_same_bytes(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    audio = b"pretend-this-is-real-webm-opus-bytes"
    upload = client.post("/media", params={"format": "webm_opus"}, content=audio)
    assert upload.status_code == 200
    body = upload.json()
    # MediaUploadOut's exact field set — contracts/chat/media.py — nothing
    # invented, nothing extra assumed.
    assert set(body.keys()) >= {"uri", "format", "duration_ms"}
    assert body["format"] == "webm_opus"
    assert body["uri"].startswith("media:")

    media_id = body["uri"].removeprefix("media:")
    fetched = client.get(f"/media/{media_id}")
    assert fetched.status_code == 200
    assert fetched.content == audio
    assert fetched.headers["content-type"] == "audio/webm"


def test_same_upload_retried_returns_the_same_reference_and_stores_one_copy(
    client, db_session, login_as
):
    # This is M1's "retry on weak network" case, and it will happen
    # constantly -- the SAME author re-POSTing the SAME bytes after a
    # dropped ack must be idempotent: same reference back, one row, one
    # file, not two.
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    audio = b"identical-bytes-sent-twice-because-the-ack-never-arrived"
    first = client.post("/media", params={"format": "ogg_opus", "duration_ms": "3000"}, content=audio)
    second = client.post("/media", params={"format": "ogg_opus", "duration_ms": "3000"}, content=audio)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["uri"] == second.json()["uri"]

    from app.db.models import MediaObject

    rows = (
        db_session.query(MediaObject)
        .filter(MediaObject.author_id == alice.id)
        .all()
    )
    assert len(rows) == 1, f"expected exactly one stored copy, found {len(rows)}"


def test_fetch_unknown_reference_404s_cleanly(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    resp = client.get(f"/media/{uuid.uuid4()}")
    assert resp.status_code == 404
    # A clean, readable error -- not a raw 500 traceback.
    assert "detail" in resp.json()


def test_fetch_malformed_id_404s_rather_than_500ing(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    resp = client.get("/media/not-a-uuid-at-all")
    assert resp.status_code == 404


def test_upload_exceeding_the_size_limit_is_rejected_with_a_readable_error(
    client, db_session, login_as
):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    max_bytes = get_settings().MEDIA_MAX_UPLOAD_BYTES
    oversized = b"x" * (max_bytes + 1)

    resp = client.post("/media", params={"format": "wav_pcm16"}, content=oversized)

    assert resp.status_code == 413
    assert "detail" in resp.json()


def test_rejected_upload_leaves_no_orphaned_row(client, db_session, login_as):
    # Companion to the size-limit test above: the store must never end up
    # with a database row for an upload that was refused -- a row with no
    # (or no matching) bytes behind it is how a store starts lying about
    # what it holds.
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    max_bytes = get_settings().MEDIA_MAX_UPLOAD_BYTES
    oversized = b"x" * (max_bytes + 1)
    client.post("/media", params={"format": "wav_pcm16"}, content=oversized)

    from app.db.models import MediaObject

    rows = db_session.query(MediaObject).filter(MediaObject.author_id == alice.id).all()
    assert rows == []


def test_rejected_upload_leaves_no_orphaned_file(client, db_session, login_as, tmp_path, monkeypatch):
    # Same guarantee as the row-level test above, checked from the other
    # side: the configured storage root must contain nothing after a
    # rejected upload, not a partial/truncated file.
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", str(tmp_path))
    from app.config import get_settings as _get_settings

    _get_settings.cache_clear()

    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    max_bytes = get_settings().MEDIA_MAX_UPLOAD_BYTES
    oversized = b"x" * (max_bytes + 1)
    client.post("/media", params={"format": "wav_pcm16"}, content=oversized)

    assert list(tmp_path.iterdir()) == []
    _get_settings.cache_clear()


def test_upload_rejects_an_unknown_format(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    resp = client.post("/media", params={"format": "flac"}, content=b"x")
    assert resp.status_code == 422


def test_upload_rejects_an_empty_body(client, db_session, login_as):
    alice = _make_db_user(db_session, "Alice")
    login_as(alice)

    resp = client.post("/media", params={"format": "mp3"}, content=b"")
    assert resp.status_code == 422
