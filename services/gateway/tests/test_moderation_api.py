"""Week 7 Phase 6: the moderator console's routes.

`contracts/chat/moderation.py` (M4's, issue #65) describes four routes: the
review queue, one message's event trail, and release / block. Everything that
file says "the gateway enforces" is enforced here: the optimistic-concurrency
guard (`expected_event_id`), the append-only trail (every decision is a NEW
event), that only a moderator or admin may use any of it, and that releasing a
message actually delivers it.

The role checked is the DATABASE role (`users.role`), not the one the token
stub derives: with stub auth the token-derived user is always `elder`, so
`require_role("moderator")` could never pass.

Written before app/moderation.py exists.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from contracts.chat.moderation import (
    ModerationAction,
    ModerationEventsOut,
    ModerationQueueOut,
    ModerationReviewOut,
)

import app.ws as ws_module
from app.db.models import Message
from app.db.models import User as DbUser
from app.db.moderation import list_moderation_events, record_moderation_event
from app.db.renderings import set_message_transcript, upsert_rendering
from app.db.repository import create_media_object, create_message, set_message_status
from app.media_storage import get_media_storage
from app.tokens import issue_token


def _user(db_session, name, role="elder", language="te"):
    user = DbUser(name=name, preferred_language=language, role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _event(db_session, message_id, **overrides):
    kwargs = {
        "message_id": message_id,
        "actor_kind": "classifier",
        "label": "D_DISPUTATIONAL",
        "action": "HOLD",
        "rationale": "Criticism of a named person.",
        "policy_version": "policy@2026-09-22",
        "confidence": 0.62,
        "model_version": "qwen",
    }
    kwargs.update(overrides)
    record_moderation_event(db_session, **kwargs)
    db_session.commit()


def _held(db_session, *, author=None, target=None, kind="text", status="held", events=1, **extra):
    """A message the pipeline has already ruled HOLD on."""
    author = author or _user(db_session, "Author")
    target = target or _user(db_session, "Target", language="hi")
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
        get_media_storage().put(str(media.id), b"voice")
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
        text="ఈ రోజు సత్సంగం?" if kind == "text" else None,
        source_lang="te",
        client_msg_id=uuid.uuid4(),
        undo_expires_at=datetime.now(UTC) - timedelta(seconds=5),
        **media_kwargs,
        **extra,
    )
    db_session.commit()
    message_id = message.id
    if kind == "voice":
        set_message_transcript(db_session, message_id, "ఈ రోజు సత్సంగం ఎప్పుడు?", "te")
    db_session.execute(
        Message.__table__.update().where(Message.id == message_id).values(pivot_text_en="When?")
    )
    db_session.commit()
    for _ in range(events):
        _event(db_session, message_id)
    if status != "pending":
        set_message_status(db_session, message_id, new_status=status, expected="pending")
        db_session.commit()
    return message_id, author, target


@pytest.fixture
def moderator(db_session):
    return _user(db_session, "Moderator", role="moderator")


@pytest.fixture
def sent_frames(monkeypatch):
    frames = []

    async def capture(recipients, frame, exclude=None):
        frames.append((list(recipients), frame))

    monkeypatch.setattr(ws_module.manager, "broadcast", capture)
    return frames


def _queue(client, **params):
    return client.get("/moderation/queue", params=params)


# --- who may use it ----------------------------------------------------------------------


def test_no_token_is_401(client):
    assert client.get("/moderation/queue").status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/moderation/queue"),
        ("get", "/moderation/messages/{id}/events"),
        ("post", "/moderation/messages/{id}/release"),
        ("post", "/moderation/messages/{id}/block"),
    ],
)
def test_an_elder_is_refused_every_route(client, db_session, login_as, method, path):
    message_id, author, _ = _held(db_session)
    login_as(author)

    resp = getattr(client, method)(
        path.format(id=message_id), **({"json": {}} if method == "post" else {})
    )

    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["moderator", "admin"])
def test_a_moderator_or_admin_may_read_the_queue(client, db_session, login_as, role):
    login_as(_user(db_session, "Staff", role=role))
    assert _queue(client).status_code == 200


def test_the_database_role_decides_not_the_token(client, db_session):
    """Stub auth derives role='elder' from any token. A user whose DB row says
    moderator must still get in, and a DB elder must not."""
    mod = _user(db_session, "Mod", role="moderator")
    elder = _user(db_session, "Elder", role="elder")
    db_session.commit()

    as_mod = client.get("/moderation/queue", headers={"Authorization": f"Bearer {mod.id}"})
    as_elder = client.get("/moderation/queue", headers={"Authorization": f"Bearer {elder.id}"})

    assert as_mod.status_code == 200
    assert as_elder.status_code == 403


# --- the queue -------------------------------------------------------------------------------


def test_the_queue_returns_a_held_text_message_with_everything_the_console_needs(
    client, db_session, login_as, moderator
):
    message_id, author, target = _held(db_session, events=2)
    login_as(moderator)

    body = _queue(client).json()
    out = ModerationQueueOut.model_validate(body)

    (item,) = out.items
    assert item.message_id == message_id
    assert item.author_id == author.id and item.author_display_name == "Author"
    assert (item.target_type, item.target_id) == ("user", target.id)
    assert item.original_text == "ఈ రోజు సత్సంగం?"
    assert item.original_language == "te"
    assert item.original_media_ref is None
    assert item.pivot_text_en == "When?"
    assert item.event_count == 2
    assert item.latest_event.action is ModerationAction.HOLD
    assert item.latest_event.actor_id is None
    assert out.next_cursor is None


def test_a_held_voice_note_carries_its_audio_reference_and_no_original_text(
    client, db_session, login_as, moderator
):
    _held(db_session, kind="voice")
    login_as(moderator)

    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    assert item.original_text is None
    assert item.original_media_ref.format.value == "webm_opus"
    assert item.original_media_ref.duration_ms == 4200
    assert item.original_language == "te"  # from the transcript


def test_a_held_voice_note_shows_the_moderator_what_the_machine_heard(
    client, db_session, login_as, moderator
):
    # M4's contract 0.6.0: `transcript` / `transcript_language`, "same field name and meaning as
    # MessageOut.transcript", and deliberately NOT folded into `original_text` (that is what the
    # sender typed; a transcript is what a machine heard, and it is the thing that can be wrong).
    _held(db_session, kind="voice")
    login_as(moderator)

    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    assert item.transcript == "ఈ రోజు సత్సంగం ఎప్పుడు?"
    assert item.transcript_language == "te"
    assert item.original_text is None, "the transcript is not passed off as what the sender typed"


def test_a_held_text_message_has_no_transcript(client, db_session, login_as, moderator):
    _held(db_session)
    login_as(moderator)

    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    assert item.transcript is None and item.transcript_language is None
    assert item.original_text == "ఈ రోజు సత్సంగం?"


def test_a_voice_note_held_before_it_was_transcribed_has_no_transcript_not_an_empty_one(
    client, db_session, login_as, moderator
):
    # E.g. a pipeline failure on the audio format (OPEN_QUESTIONS #2/#19): held, nothing heard yet.
    message_id, _, _ = _held(db_session, kind="voice")
    db_session.execute(
        Message.__table__.update()
        .where(Message.id == message_id)
        .values(transcript=None, transcript_language=None)
    )
    db_session.commit()
    login_as(moderator)

    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    assert item.transcript is None and item.transcript_language is None
    assert item.original_media_ref is not None, "the recording is still there to listen to"


def test_the_audio_the_queue_points_at_is_fetchable_by_the_moderator(
    client, db_session, login_as, moderator
):
    _held(db_session, kind="voice")
    login_as(moderator)
    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    media_id = item.original_media_ref.uri.removeprefix("media:")
    # the moderator allowance needs a SIGNED token (tests/test_media_moderator_identity.py)
    resp = client.get(
        f"/media/{media_id}", headers={"Authorization": f"Bearer {issue_token(moderator.id)}"}
    )

    assert resp.status_code == 200


def test_the_latest_event_is_the_one_that_put_it_in_the_queue(
    client, db_session, login_as, moderator
):
    message_id, _, _ = _held(db_session, events=1)
    _event(db_session, message_id, action="HOLD", rationale="the newer reason")
    login_as(moderator)

    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    assert item.latest_event.rationale == "the newer reason"
    assert item.event_count == 2


def test_a_degraded_hold_is_flagged_so_the_console_shows_it_differently(
    client, db_session, login_as, moderator
):
    message_id, _, _ = _held(db_session, events=0)
    _event(db_session, message_id, confidence=0.0, degraded=True, degraded_reason="model_fallback")
    login_as(moderator)

    (item,) = ModerationQueueOut.model_validate(_queue(client).json()).items

    assert item.latest_event.degraded is True


@pytest.mark.parametrize("status", ["pending", "sent", "blocked", "cancelled", "delivered"])
def test_only_held_messages_are_in_the_queue(client, db_session, login_as, moderator, status):
    _held(db_session, status=status)
    login_as(moderator)
    assert ModerationQueueOut.model_validate(_queue(client).json()).items == []


def test_a_deleted_held_message_is_not_in_the_queue(client, db_session, login_as, moderator):
    message_id, _, _ = _held(db_session)
    db_session.get(Message, message_id).deleted_at = datetime.now(UTC)
    db_session.commit()
    login_as(moderator)
    assert ModerationQueueOut.model_validate(_queue(client).json()).items == []


def test_a_held_message_with_no_events_is_left_out_rather_than_breaking_the_contract(
    client, db_session, login_as, moderator
):
    # latest_event is required by the contract; a held row nobody ruled on
    # (say, set by hand) cannot be rendered as a queue item.
    _held(db_session, events=0)
    login_as(moderator)
    assert ModerationQueueOut.model_validate(_queue(client).json()).items == []


def test_the_queue_is_oldest_first_and_pages_with_a_cursor(client, db_session, login_as, moderator):
    ids = [_held(db_session)[0] for _ in range(5)]
    ordered = sorted(ids)
    login_as(moderator)

    first = ModerationQueueOut.model_validate(_queue(client, limit=2).json())
    assert [i.message_id for i in first.items] == ordered[:2]
    assert first.next_cursor
    second = ModerationQueueOut.model_validate(
        _queue(client, limit=2, cursor=first.next_cursor).json()
    )
    assert [i.message_id for i in second.items] == ordered[2:4]
    third = ModerationQueueOut.model_validate(
        _queue(client, limit=2, cursor=second.next_cursor).json()
    )
    assert [i.message_id for i in third.items] == ordered[4:]
    assert third.next_cursor is None


def test_releasing_an_item_between_pages_neither_skips_nor_repeats(
    client, db_session, login_as, moderator
):
    """The reason the contract says cursor, not offset: the queue changes under
    the reader while moderators work it."""
    ids = sorted(_held(db_session)[0] for _ in range(4))
    login_as(moderator)
    first = ModerationQueueOut.model_validate(_queue(client, limit=2).json())
    set_message_status(db_session, ids[0], new_status="sent", expected="held")
    db_session.commit()

    second = ModerationQueueOut.model_validate(
        _queue(client, limit=2, cursor=first.next_cursor).json()
    )

    assert [i.message_id for i in second.items] == ids[2:]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"cursor": "!!not-a-cursor!!"}])
def test_a_bad_limit_or_cursor_is_a_422(client, db_session, login_as, moderator, params):
    login_as(moderator)
    assert _queue(client, **params).status_code == 422


# --- one message's trail ------------------------------------------------------------------------


def test_the_trail_is_oldest_first(client, db_session, login_as, moderator):
    message_id, _, _ = _held(db_session, events=1)
    _event(db_session, message_id, rationale="second")
    login_as(moderator)

    resp = client.get(f"/moderation/messages/{message_id}/events")

    out = ModerationEventsOut.model_validate(resp.json())
    assert out.message_id == message_id
    assert [e.rationale for e in out.events][-1] == "second"
    assert len(out.events) == 2


def test_the_trail_of_an_unknown_message_is_404(client, db_session, login_as, moderator):
    login_as(moderator)
    assert client.get(f"/moderation/messages/{uuid.uuid4()}/events").status_code == 404


def test_a_malformed_message_id_is_a_422(client, db_session, login_as, moderator):
    login_as(moderator)
    assert client.get("/moderation/messages/not-a-uuid/events").status_code == 422


# --- release -----------------------------------------------------------------------------------------


def _release(client, message_id, **body):
    return client.post(f"/moderation/messages/{message_id}/release", json=body)


def _block(client, message_id, **body):
    return client.post(f"/moderation/messages/{message_id}/block", json=body)


def test_releasing_a_held_message_delivers_it_and_records_the_decision(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _author, _target = _held(db_session)
    login_as(moderator)

    resp = _release(client, message_id, note="Devotional idiom, not a dispute.")

    assert resp.status_code == 200
    out = ModerationReviewOut.model_validate(resp.json())
    assert out.message_status == "sent"
    assert out.event.actor_id == moderator.id
    assert out.event.action is ModerationAction.ALLOW
    assert out.event.note == "Devotional idiom, not a dispute."
    assert out.event.confidence is None and out.event.model_version is None
    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "sent"
    events = list_moderation_events(db_session, message_id)
    assert [e.actor_kind for e in events] == ["classifier", "moderator"], "append-only: a NEW event"


def test_a_release_pushes_message_new_to_the_recipient(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _author, target = _held(db_session)
    login_as(moderator)

    _release(client, message_id)

    (recipients, frame) = next(f for f in sent_frames if f[1]["type"] == "message.new")
    assert str(target.id) in recipients
    assert frame["data"]["id"] == str(message_id) and frame["data"]["status"] == "sent"


def test_a_released_messages_stored_renderings_become_visible(
    client, db_session, login_as, moderator, sent_frames
):
    """Held messages were rendered while pending (stored, hidden until out), so
    a release is a status flip -- the renderings appear without a re-run."""
    from app.db.models import MessageRendering as Row

    author, target = _user(db_session, "A"), _user(db_session, "B", language="hi")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="text",
        text="hello",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    upsert_rendering(
        db_session, message_id=message.id, language="hi", text="नमस्ते", degraded_reason="text_only"
    )
    db_session.commit()
    message_id = message.id
    _event(db_session, message_id)
    set_message_status(db_session, message_id, new_status="held", expected="pending")
    db_session.commit()
    assert db_session.query(Row).count() == 1
    login_as(moderator)

    _release(client, message_id)

    frame = next(f for f in sent_frames if f[1]["type"] == "message.new")[1]
    assert [r["language"] for r in frame["data"]["renderings"]] == ["hi"]


def test_a_label_correction_is_recorded_on_the_event(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session)
    login_as(moderator)

    resp = _release(client, message_id, label="A_DEVOTIONAL")

    out = ModerationReviewOut.model_validate(resp.json())
    assert out.event.label.value == "A_DEVOTIONAL"


def test_without_a_correction_the_classifiers_label_stands(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session)
    login_as(moderator)

    out = ModerationReviewOut.model_validate(_release(client, message_id).json())

    assert out.event.label.value == "D_DISPUTATIONAL"
    assert out.event.policy_version == "policy@2026-09-22"


def test_the_response_says_the_sender_has_not_been_told_because_nothing_can_tell_them_yet(
    client, db_session, login_as, moderator, sent_frames
):
    """notice_sent is a promise about the SENDER's experience. There is no wire
    surface for a notice yet (OPEN_QUESTIONS #18), so claiming True would be a
    lie the console would then hide."""
    message_id, _, _ = _held(db_session)
    login_as(moderator)

    out = ModerationReviewOut.model_validate(_block(client, message_id).json())

    assert out.notice_sent is False


def test_a_blocked_message_can_be_released_as_a_reversal(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session, status="blocked")
    login_as(moderator)

    resp = _release(client, message_id)

    assert resp.status_code == 200
    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "sent"


@pytest.mark.parametrize("status", ["pending", "sent", "delivered", "cancelled"])
def test_only_a_held_or_blocked_message_can_be_released(
    client, db_session, login_as, moderator, status
):
    message_id, _, _ = _held(db_session, status=status)
    login_as(moderator)

    resp = _release(client, message_id)

    assert resp.status_code == 409
    assert len(list_moderation_events(db_session, message_id)) == 1, "nothing was recorded"


def test_a_second_release_of_the_same_message_is_a_conflict(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session)
    login_as(moderator)

    assert _release(client, message_id).status_code == 200
    assert _release(client, message_id).status_code == 409
    assert len([f for f in sent_frames if f[1]["type"] == "message.new"]) == 1, "delivered once"


def test_two_moderators_acting_at_once_cannot_both_win(
    client, db_session, login_as, moderator, sent_frames, monkeypatch
):
    """The sequential 'second release is a 409' test never reaches the atomic
    claim: the status pre-check refuses first. This is the real race: the
    other moderator's decision lands AFTER this request read the message and
    BEFORE it claims it. Only the conditional UPDATE stands between them."""
    from sqlalchemy import text

    import app.moderation as moderation_module

    message_id, _, _ = _held(db_session)
    real = moderation_module.latest_moderation_event

    def read_then_lose_the_race(session, mid):
        event = real(session, mid)
        # The other moderator blocks it now (raw SQL: the session's objects
        # keep believing it is still held, exactly as a concurrent request's
        # would).
        session.execute(
            text("update messages set status = 'blocked' where id = :i"), {"i": message_id}
        )
        return event

    monkeypatch.setattr(moderation_module, "latest_moderation_event", read_then_lose_the_race)
    login_as(moderator)

    resp = _release(client, message_id)

    assert resp.status_code == 409
    db_session.rollback()
    assert len(list_moderation_events(db_session, message_id)) == 1, "the loser records nothing"
    assert [f for f in sent_frames if f[1]["type"] == "message.new"] == [], "and delivers nothing"


def test_an_unknown_message_is_404(client, db_session, login_as, moderator):
    login_as(moderator)
    assert _release(client, uuid.uuid4()).status_code == 404


# --- optimistic concurrency ---------------------------------------------------------------------------


def test_a_stale_expected_event_id_is_rejected_and_nothing_changes(
    client, db_session, login_as, moderator, sent_frames
):
    """Two moderators open the same item; the second's click must not silently
    overwrite a decision they never saw."""
    message_id, _, _ = _held(db_session)
    stale = list_moderation_events(db_session, message_id)[-1].id
    _event(db_session, message_id, rationale="a newer ruling arrived")
    login_as(moderator)

    resp = _release(client, message_id, expected_event_id=str(stale))

    assert resp.status_code == 409
    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "held"
    assert len(list_moderation_events(db_session, message_id)) == 2


def test_the_current_expected_event_id_is_accepted(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session)
    current = list_moderation_events(db_session, message_id)[-1].id
    login_as(moderator)

    assert _release(client, message_id, expected_event_id=str(current)).status_code == 200


def test_omitting_expected_event_id_is_allowed_for_a_non_interactive_caller(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session)
    login_as(moderator)
    assert _release(client, message_id).status_code == 200


def test_a_note_over_2000_characters_is_a_422(client, db_session, login_as, moderator):
    message_id, _, _ = _held(db_session)
    login_as(moderator)
    assert _release(client, message_id, note="x" * 2001).status_code == 422


# --- block ----------------------------------------------------------------------------------------------


def test_blocking_a_held_message_blocks_it_and_tells_the_sender_the_status_changed(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, author, _target = _held(db_session)
    login_as(moderator)

    resp = _block(client, message_id, note="Harmful.")

    out = ModerationReviewOut.model_validate(resp.json())
    assert out.message_status == "blocked"
    assert out.event.action is ModerationAction.BLOCK and out.event.note == "Harmful."
    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "blocked"
    status_frames = [f for f in sent_frames if f[1]["type"] == "message.status"]
    assert [recipients for recipients, _ in status_frames] == [[str(author.id)]]
    assert status_frames[0][1]["data"] == {
        "contract_version": status_frames[0][1]["data"]["contract_version"],
        "id": str(message_id),
        "status": "blocked",
        "delivered_count": None,
        "member_count": None,
        # Added by contracts/chat 0.6.0 (issue #65's "sender notice" answer).
        # Still None here: this test asserts the status frame is sent, and
        # nothing populates the notice yet -- that is the gateway work this
        # contract change unblocks. When it lands, these become the blocked
        # notice and its language, and this assertion is where that shows.
        "notice_text": None,
        "notice_language": None,
    }
    assert [f for f in sent_frames if f[1]["type"] == "message.new"] == [], "never delivered"


@pytest.mark.parametrize("status", ["pending", "sent", "blocked", "cancelled"])
def test_only_a_held_message_can_be_blocked(client, db_session, login_as, moderator, status):
    message_id, _, _ = _held(db_session, status=status)
    login_as(moderator)
    assert _block(client, message_id).status_code == 409


def test_a_release_after_a_block_is_a_second_decision_not_an_edit(
    client, db_session, login_as, moderator, sent_frames
):
    message_id, _, _ = _held(db_session)
    login_as(moderator)
    _block(client, message_id)
    _release(client, message_id)

    kinds = [(e.actor_kind, e.action) for e in list_moderation_events(db_session, message_id)]

    assert kinds == [("classifier", "HOLD"), ("moderator", "BLOCK"), ("moderator", "ALLOW")]
