"""OPEN_QUESTIONS #20: nothing detects a message stuck behind a dead pipeline.

A message is STUCK when its pipeline is `pending` and nothing is going to finish it: no
`process_message` job that is queued (a retry waiting out its backoff counts) or running. That is
deliberately NOT "pending for longer than N seconds": the load runs (infra/ai/README.md) showed
legitimate waits of several minutes with a live job, and an age rule would hold those. A stuck
message is handed to the same fail-closed path a dead-lettered job uses, so it becomes `held` with a
SYSTEM event and appears in the moderator queue the people already look at -- no new endpoint, no
new service, no contract change.

Written before app/pipeline_watchdog.py exists. Every assertion reads the observable result (the
message's status, its events, what a recipient can read), never that a function was called.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app import pipeline
from app.config import get_settings
from app.db.models import Job, Message
from app.db.models import User as DbUser
from app.db.moderation import list_moderation_events, list_moderation_queue
from app.db.repository import create_media_object, create_message, get_visible_messages_since
from app.pipeline_watchdog import hold_stuck_messages, run_pipeline_watchdog_loop

GRACE = 120.0
SECRET_TEXT = "ఇది రహస్య సందేశం, ఎవరికీ చూపించవద్దు"
SECRET_TRANSCRIPT = "the transcript of a private voice note"


@pytest.fixture
def outbox(monkeypatch):
    """What the pipeline asks the event loop to tell the sender, recorded instead of done."""
    box: list[tuple[uuid.UUID, str]] = []
    monkeypatch.setattr(pipeline, "_notify_author", lambda mid, status: box.append((mid, status)))
    return box


def _user(db_session, name):
    user = DbUser(name=name, preferred_language="te", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _dm(db_session, *, age_seconds=600, text=SECRET_TEXT):
    """A text DM whose pipeline has been started (so it has its job), `age_seconds` old."""
    author, target = _user(db_session, "Author"), _user(db_session, "Target")
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="text",
        text=text,
        source_lang="te",
        client_msg_id=uuid.uuid4(),
        undo_expires_at=datetime.now(UTC) + timedelta(seconds=300),
    )
    pipeline.start_pipeline(db_session, message)
    db_session.commit()
    _age(db_session, message.id, age_seconds)
    return message.id, author.id, target.id


def _voice_dm(db_session, *, age_seconds=600):
    """A voice DM with a transcript (the two columns go together, and only voice has them)."""
    author, target = _user(db_session, "VoiceAuthor"), _user(db_session, "VoiceTarget")
    media, _ = create_media_object(
        db_session,
        media_id=uuid.uuid4(),
        author_id=author.id,
        format="wav_pcm16",
        sha256_hex=uuid.uuid4().hex,
        size_bytes=10,
        duration_ms=4200,
    )
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="voice",
        source_lang="te",
        client_msg_id=uuid.uuid4(),
        original_media_ref=f"media:{media.id}",
        media_object_id=media.id,
        media_format="wav_pcm16",
        media_duration_ms=4200,
        undo_expires_at=datetime.now(UTC) + timedelta(seconds=300),
    )
    pipeline.start_pipeline(db_session, message)
    db_session.execute(
        update(Message)
        .where(Message.id == message.id)
        .values(transcript=SECRET_TRANSCRIPT, transcript_language="te")
    )
    db_session.commit()
    _age(db_session, message.id, age_seconds)
    return message.id


def _age(db_session, message_id, seconds):
    db_session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(created_at=datetime.now(UTC) - timedelta(seconds=seconds))
    )
    db_session.commit()


def _job(db_session, message_id) -> Job:
    return db_session.scalars(
        select(Job).where(Job.payload["message_id"].astext == str(message_id))
    ).one()


def _set_job(db_session, message_id, status):
    db_session.execute(
        update(Job).where(Job.id == _job(db_session, message_id).id).values(status=status)
    )
    db_session.commit()


def _delete_job(db_session, message_id):
    db_session.delete(_job(db_session, message_id))
    db_session.commit()


def _msg(db_session, message_id) -> Message:
    db_session.expire_all()
    return db_session.get(Message, message_id)


# --- what is stuck -----------------------------------------------------------------------


def test_a_pending_message_whose_job_is_gone_is_held_with_a_system_event(db_session, outbox):
    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)

    held = hold_stuck_messages(grace_seconds=GRACE)

    assert held == [str(message_id)]
    message = _msg(db_session, message_id)
    assert message.status == "held"
    assert message.pipeline_state == "failed"
    events = list_moderation_events(db_session, message_id)
    assert [(e.actor_kind, e.action) for e in events] == [("system", "HOLD")]
    assert "stalled" in events[0].rationale.lower()
    assert outbox == [(message_id, "held")], "the sender's devices are told it is held"


def test_a_dead_job_whose_hook_never_ran_is_stuck(db_session, outbox):
    # The dead-letter hook holds the message when its job dies; if the hook itself failed (it is
    # logged, never raised) the job is `dead` and the message is still pending, forever.
    message_id, *_ = _dm(db_session)
    _set_job(db_session, message_id, "dead")

    assert hold_stuck_messages(grace_seconds=GRACE) == [str(message_id)]
    assert _msg(db_session, message_id).status == "held"


def test_a_done_job_that_left_the_pipeline_pending_is_stuck(db_session, outbox):
    message_id, *_ = _dm(db_session)
    _set_job(db_session, message_id, "done")

    assert hold_stuck_messages(grace_seconds=GRACE) == [str(message_id)]


# --- what is NOT stuck (a healthy pipeline reports nothing) -------------------------------


@pytest.mark.parametrize("job_status", ["queued", "running"])
def test_a_slow_message_with_a_live_job_is_left_alone_however_old_it_is(
    db_session, outbox, job_status
):
    # Queued behind other notes, or being worked: slow, not stuck. A day old on purpose.
    message_id, *_ = _dm(db_session, age_seconds=86_400)
    _set_job(db_session, message_id, job_status)

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    message = _msg(db_session, message_id)
    assert message.status == "pending" and message.pipeline_state == "pending"
    assert list_moderation_events(db_session, message_id) == []
    assert outbox == []


def test_a_young_orphan_is_given_the_grace_period(db_session, outbox):
    message_id, *_ = _dm(db_session, age_seconds=10)
    _delete_job(db_session, message_id)

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    assert _msg(db_session, message_id).status == "pending"


@pytest.mark.parametrize(
    ("status", "state"),
    [("sent", "complete"), ("held", "complete"), ("cancelled", "pending"), ("pending", "complete")],
)
def test_messages_that_are_finished_or_no_longer_pending_are_never_touched(
    db_session, outbox, status, state
):
    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)  # even with no job at all
    db_session.execute(
        update(Message).where(Message.id == message_id).values(status=status, pipeline_state=state)
    )
    db_session.commit()

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    after = _msg(db_session, message_id)
    assert (after.status, after.pipeline_state) == (status, state)
    assert list_moderation_events(db_session, message_id) == []


def test_a_message_that_never_had_a_pipeline_is_not_stuck(db_session, outbox):
    # PIPELINE_ENABLED=false: pipeline_state stays NULL and the gateway delivers as before.
    author, target = _user(db_session, "A"), _user(db_session, "B")
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
    _age(db_session, message.id, 86_400)

    assert hold_stuck_messages(grace_seconds=GRACE) == []


def test_a_second_pass_changes_nothing(db_session, outbox):
    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)

    assert hold_stuck_messages(grace_seconds=GRACE) == [str(message_id)]
    assert hold_stuck_messages(grace_seconds=GRACE) == []
    assert len(list_moderation_events(db_session, message_id)) == 1
    assert outbox == [(message_id, "held")]


def test_one_stuck_message_among_healthy_ones_is_the_only_one_held(db_session, outbox):
    stuck, *_ = _dm(db_session)
    _delete_job(db_session, stuck)
    healthy, *_ = _dm(db_session)  # live queued job, same age

    assert hold_stuck_messages(grace_seconds=GRACE) == [str(stuck)]
    assert _msg(db_session, healthy).status == "pending"


# --- the scan itself, and the things only a second session can show ----------------------------


def test_the_scan_lists_only_the_orphan(db_session):
    from app.pipeline_watchdog import find_stuck_message_ids

    orphan, *_ = _dm(db_session)
    _delete_job(db_session, orphan)
    queued, *_ = _dm(db_session)  # live job
    running, *_ = _dm(db_session)
    _set_job(db_session, running, "running")
    finished, *_ = _dm(db_session)
    _delete_job(db_session, finished)
    db_session.execute(
        update(Message)
        .where(Message.id == finished)
        .values(status="sent", pipeline_state="complete")
    )
    # One message per filter in the scan, each with no live job so that ONLY that filter keeps it
    # out: the hold re-checks status, state and deletion, which would otherwise hide a missing one.
    cancelled, *_ = _dm(db_session)
    _delete_job(db_session, cancelled)
    db_session.execute(update(Message).where(Message.id == cancelled).values(status="cancelled"))
    complete, *_ = _dm(db_session)
    _delete_job(db_session, complete)
    db_session.execute(
        update(Message).where(Message.id == complete).values(pipeline_state="complete")
    )
    deleted, *_ = _dm(db_session)
    _delete_job(db_session, deleted)
    db_session.execute(
        update(Message).where(Message.id == deleted).values(deleted_at=datetime.now(UTC))
    )
    db_session.commit()

    assert find_stuck_message_ids(db_session, grace_seconds=GRACE) == [orphan]
    assert queued not in find_stuck_message_ids(db_session, grace_seconds=GRACE)


def test_a_deleted_message_is_left_alone(db_session, outbox):
    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)
    db_session.execute(
        update(Message).where(Message.id == message_id).values(deleted_at=datetime.now(UTC))
    )
    db_session.commit()

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    assert _msg(db_session, message_id).status == "pending"
    assert list_moderation_events(db_session, message_id) == []


def test_a_message_deleted_after_the_scan_is_not_held(db_session, outbox, monkeypatch):
    import app.pipeline_watchdog as watchdog

    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)
    db_session.execute(
        update(Message).where(Message.id == message_id).values(deleted_at=datetime.now(UTC))
    )
    db_session.commit()
    monkeypatch.setattr(watchdog, "find_stuck_message_ids", lambda *a, **k: [message_id])

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    assert list_moderation_events(db_session, message_id) == []


def test_a_message_someone_else_is_working_on_right_now_is_skipped_not_waited_for(
    db_session, engine, outbox
):
    """Another transaction holds the row (the sender undoing it, say): the watchdog must not queue
    up behind it or write an event for a message it then cannot change. It skips, and the next
    pass sees the message."""
    from sqlalchemy.orm import Session

    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)

    other = Session(engine)
    try:
        other.scalars(select(Message).where(Message.id == message_id).with_for_update()).one()

        assert hold_stuck_messages(grace_seconds=GRACE) == []
        assert list_moderation_events(db_session, message_id) == []
    finally:
        other.rollback()
        other.close()

    assert hold_stuck_messages(grace_seconds=GRACE) == [str(message_id)], "the next pass holds it"


# --- the race between the scan and the hold ---------------------------------------------------
# The scan and the hold are separate transactions; a message can change in between. The hold
# re-checks under a row lock. Simulated by making the scan report a message that is no longer
# (or never was) stuck.


def test_a_message_that_finished_after_the_scan_is_not_held(db_session, outbox, monkeypatch):
    import app.pipeline_watchdog as watchdog

    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)
    db_session.execute(
        update(Message)
        .where(Message.id == message_id)
        .values(status="cancelled")  # the sender undid it after the scan
    )
    db_session.commit()
    monkeypatch.setattr(watchdog, "find_stuck_message_ids", lambda *a, **k: [message_id])

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    assert _msg(db_session, message_id).status == "cancelled"
    assert list_moderation_events(db_session, message_id) == []
    assert outbox == []


def test_a_message_that_got_a_live_job_after_the_scan_is_not_held(db_session, outbox, monkeypatch):
    import app.pipeline_watchdog as watchdog

    message_id, *_ = _dm(db_session)  # still has its queued job
    monkeypatch.setattr(watchdog, "find_stuck_message_ids", lambda *a, **k: [message_id])

    assert hold_stuck_messages(grace_seconds=GRACE) == []
    assert _msg(db_session, message_id).status == "pending"
    assert list_moderation_events(db_session, message_id) == []


# --- where the signal goes, and who can read it ---------------------------------------------


def test_the_held_message_appears_in_the_moderator_queue(db_session, outbox):
    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)
    hold_stuck_messages(grace_seconds=GRACE)

    queue = list_moderation_queue(db_session)

    assert [(m.id, event.actor_kind) for m, _author, event, _n in queue] == [(message_id, "system")]


def test_the_recipient_cannot_read_a_stuck_message_before_or_after_it_is_held(db_session, outbox):
    message_id, author_id, target_id = _dm(db_session)
    _delete_job(db_session, message_id)
    conversation_id = _msg(db_session, message_id).conversation_id

    def recipient_sees():
        db_session.expire_all()
        return get_visible_messages_since(
            db_session, conversation_id=conversation_id, viewer_id=target_id
        )

    assert recipient_sees() == []
    hold_stuck_messages(grace_seconds=GRACE)
    assert recipient_sees() == [], "held means withheld"
    db_session.expire_all()
    own = get_visible_messages_since(
        db_session, conversation_id=conversation_id, viewer_id=author_id
    )
    assert [m.id for m in own] == [message_id], "the author still sees their own message"


def _assert_no_content_leaks(db_session, message_id, caplog, *secrets):
    assert str(message_id) in caplog.text, "a human reading the log can find the message"
    (event,) = list_moderation_events(db_session, message_id)
    for secret in secrets:
        assert secret not in caplog.text
        for field in (event.rationale, event.note, event.notice_text, event.label):
            assert secret not in (field or "")


def test_neither_the_log_nor_the_event_carries_a_text_messages_words(db_session, outbox, caplog):
    message_id, *_ = _dm(db_session, text=SECRET_TEXT)
    _delete_job(db_session, message_id)

    with caplog.at_level(logging.INFO, logger="app.pipeline_watchdog"):
        hold_stuck_messages(grace_seconds=GRACE)

    _assert_no_content_leaks(db_session, message_id, caplog, SECRET_TEXT)


def test_neither_the_log_nor_the_event_carries_a_voice_notes_transcript(db_session, outbox, caplog):
    message_id = _voice_dm(db_session)
    _delete_job(db_session, message_id)
    assert _msg(db_session, message_id).transcript == SECRET_TRANSCRIPT, "the setup has one"

    with caplog.at_level(logging.INFO, logger="app.pipeline_watchdog"):
        hold_stuck_messages(grace_seconds=GRACE)

    assert _msg(db_session, message_id).status == "held"
    _assert_no_content_leaks(db_session, message_id, caplog, SECRET_TRANSCRIPT)


# --- the loop --------------------------------------------------------------------------------


def test_the_loop_holds_a_stuck_message_and_stops_when_asked(db_session, outbox, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "PIPELINE_WATCHDOG_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(settings, "PIPELINE_STUCK_GRACE_SECONDS", 0.0)
    message_id, *_ = _dm(db_session)
    _delete_job(db_session, message_id)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(run_pipeline_watchdog_loop(stop))
        await asyncio.sleep(0.5)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(run())

    assert _msg(db_session, message_id).status == "held"


def test_a_failing_pass_does_not_kill_the_loop(db_session, outbox, monkeypatch):
    import app.pipeline_watchdog as watchdog

    settings = get_settings()
    monkeypatch.setattr(settings, "PIPELINE_WATCHDOG_INTERVAL_SECONDS", 0.05)
    calls = []

    def boom(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("database went away")

    monkeypatch.setattr(watchdog, "hold_stuck_messages", boom)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(run_pipeline_watchdog_loop(stop))
        await asyncio.sleep(0.4)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(run())

    assert len(calls) >= 2, "it kept trying after the first failure"
