"""A message is created `pending` and its delivery is scheduled in
app/undo.py's in-memory registry. If the gateway process dies or restarts
inside (or after) that window, the scheduled task dies with it and the
message stays `pending` forever: it is never delivered, and the sender's
"sent" tick never arrives. That is the one place a note could still be lost
after this week's durable job queue -- app/undo.py's own docstring names it.

app/recovery.py::recover_pending_fan_outs runs once at startup and
re-schedules delivery for every still-`pending` message. It is safe by
construction because fan_out_message only acts on a `pending` message via an
atomic status transition: running it twice, or racing a second gateway
process doing the same, delivers at most once.

Written before app/recovery.py exists.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import sessionmaker

import app.undo as undo_module
from app.db.models import Message
from app.db.models import User as DbUser
from app.db.repository import create_message, set_message_status
from app.recovery import recover_pending_fan_outs


def _make_db_user(db_session, name="User"):
    user = DbUser(name=name, preferred_language="en", role="elder")
    db_session.add(user)
    db_session.flush()
    return user


def _pending_message(db_session, *, undo_expires_at):
    alice = _make_db_user(db_session, "Alice")
    bob = _make_db_user(db_session, "Bob")
    message = create_message(
        db_session,
        author_id=alice.id,
        target_type="user",
        target_user_id=bob.id,
        kind="text",
        text="hello",
        client_msg_id=uuid.uuid4(),
        undo_expires_at=undo_expires_at,
    )
    db_session.commit()
    return message.id


async def _drain_scheduled():
    tasks = list(undo_module._pending.values())
    if tasks:
        await asyncio.gather(*tasks)


async def test_a_message_left_pending_by_a_restart_is_delivered(db_session, engine, monkeypatch):
    # undo_expires_at already in the past: the window elapsed while the
    # process was down. undo._pending is empty, exactly as after a restart.
    message_id = _pending_message(
        db_session, undo_expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    undo_module._pending.clear()

    async def instant_sleep(_delay):
        return None

    monkeypatch.setattr(undo_module, "asyncio_sleep", instant_sleep)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    scheduled = await recover_pending_fan_outs(factory)
    await _drain_scheduled()

    assert scheduled == 1
    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "sent"


async def test_a_message_still_inside_its_undo_window_waits_only_the_remainder(
    db_session, engine, monkeypatch
):
    _pending_message(db_session, undo_expires_at=datetime.now(UTC) + timedelta(seconds=20))
    undo_module._pending.clear()
    delays: list[float] = []

    async def recording_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(undo_module, "asyncio_sleep", recording_sleep)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    await recover_pending_fan_outs(factory)
    await _drain_scheduled()

    assert len(delays) == 1
    assert 15 < delays[0] <= 20, (
        f"should wait the remaining ~20s, not a fresh full window: {delays}"
    )


async def test_messages_that_are_not_pending_are_left_alone(db_session, engine, monkeypatch):
    message_id = _pending_message(
        db_session, undo_expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    set_message_status(db_session, message_id, new_status="cancelled", expected="pending")
    db_session.commit()
    undo_module._pending.clear()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    scheduled = await recover_pending_fan_outs(factory)
    await _drain_scheduled()

    assert scheduled == 0
    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "cancelled"


async def test_running_recovery_twice_delivers_once(db_session, engine, monkeypatch):
    message_id = _pending_message(
        db_session, undo_expires_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    undo_module._pending.clear()

    async def instant_sleep(_delay):
        return None

    monkeypatch.setattr(undo_module, "asyncio_sleep", instant_sleep)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    await recover_pending_fan_outs(factory)
    await recover_pending_fan_outs(factory)  # a second process (or a retry) doing the same
    await _drain_scheduled()

    db_session.expire_all()
    assert db_session.get(Message, message_id).status == "sent"
