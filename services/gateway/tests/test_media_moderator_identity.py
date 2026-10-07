"""Phase 3a follow-up: a moderator may hear a HELD message's audio only with a SIGNED token.

Same hole as the /moderation routes (OPEN_QUESTIONS #23), on a route that was already public:
`user_can_fetch_media` lets `users.role` moderator/admin fetch the audio of a held or blocked message,
and in the default `AUTH_MODE=legacy` that identity is a bare UUID, so anyone who knows a moderator's
id could download held voice notes through `GET /media/{id}`.

Narrow on purpose: authors and recipients are unchanged (the elder app authenticates with a bare UUID
today, #32), and a refused moderator still gets the same 404 as a missing id.
"""

import uuid

from app.config import get_settings
from app.db.models import User as DbUser
from app.db.repository import create_media_object, create_message, set_message_status
from app.media_storage import get_media_storage
from app.tokens import issue_token


def _user(db_session, name, role="elder"):
    user = DbUser(name=name, preferred_language="en", role=role)
    db_session.add(user)
    db_session.commit()
    return user


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _voice(db_session, *, author, target, status):
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author.id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=1000,
    )
    db_session.commit()
    media_id = media.id
    get_media_storage().put(str(media_id), b"the-note")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="voice",
        original_media_ref=f"media:{media_id}",
        media_duration_ms=1000,
        media_object_id=media_id,
        media_format="wav_pcm16",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    set_message_status(db_session, message.id, new_status=status, expected="pending")
    db_session.commit()
    return media_id


def test_a_moderators_bare_uuid_cannot_hear_a_held_message(client, db_session):
    assert get_settings().AUTH_MODE == "legacy"  # the default: this is the exposed configuration
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    mod = _user(db_session, "Mod", "moderator")
    media_id = _voice(db_session, author=alice, target=bob, status="held")

    resp = client.get(f"/media/{media_id}", headers=_hdr(mod.id))

    assert resp.status_code == 404
    assert resp.json() == client.get(f"/media/{uuid.uuid4()}", headers=_hdr(mod.id)).json()


def test_an_admins_bare_uuid_cannot_hear_a_blocked_message_either(client, db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    admin = _user(db_session, "Admin", "admin")
    media_id = _voice(db_session, author=alice, target=bob, status="blocked")

    assert client.get(f"/media/{media_id}", headers=_hdr(admin.id)).status_code == 404


def test_a_signed_moderator_token_can_hear_it(client, db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    mod = _user(db_session, "Mod", "moderator")
    media_id = _voice(db_session, author=alice, target=bob, status="held")

    resp = client.get(f"/media/{media_id}", headers=_hdr(issue_token(mod.id)))

    assert resp.status_code == 200
    assert resp.content == b"the-note"


def test_a_signed_token_for_a_non_moderator_still_cannot(client, db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    carol = _user(db_session, "Carol")
    media_id = _voice(db_session, author=alice, target=bob, status="held")

    assert client.get(f"/media/{media_id}", headers=_hdr(issue_token(carol.id))).status_code == 404


def test_the_author_with_a_bare_uuid_still_hears_their_own_held_note(client, db_session):
    """The elder app authenticates with a bare UUID today: authors must not be locked out."""
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    media_id = _voice(db_session, author=alice, target=bob, status="held")

    resp = client.get(f"/media/{media_id}", headers=_hdr(alice.id))

    assert resp.status_code == 200


def test_a_recipient_with_a_bare_uuid_still_hears_a_delivered_note(client, db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    media_id = _voice(db_session, author=alice, target=bob, status="sent")

    assert client.get(f"/media/{media_id}", headers=_hdr(bob.id)).status_code == 200


def test_the_repository_function_fails_closed_when_not_told_the_token_was_signed(db_session):
    """The route always passes the flag, so only a direct call can pin the default."""
    from app.db.models import MediaObject
    from app.db.repository import user_can_fetch_media

    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    mod = _user(db_session, "Mod", "moderator")
    media_id = _voice(db_session, author=alice, target=bob, status="held")
    media = db_session.get(MediaObject, media_id)

    assert user_can_fetch_media(db_session, media, mod.id) is False
    assert user_can_fetch_media(db_session, media, mod.id, moderator_may_review=False) is False
    assert user_can_fetch_media(db_session, media, mod.id, moderator_may_review=True) is True
