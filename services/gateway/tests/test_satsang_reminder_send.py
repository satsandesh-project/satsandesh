"""The reminder's SEND under the worst moments (OPEN_QUESTIONS #49).

Measured before this change (REAL jobs worker and handler, loopback stand-ins for the push service, a
throwaway Postgres; the numbers are in the pull request): the handler sent every push inside one
transaction with the satsang row locked and a pooled connection held, `webpush()` had no timeout, and
Postgres kills a connection idle in a transaction for 30 s. So ONE subscriber whose push service accepts
the connection and never answers wedged the worker for good (the job stayed `running`, the later
subscribers never got the reminder), and one whose address swallows packets failed the job after 76 s with
the connection already dead, so the retry pushed the earlier subscribers AGAIN.

The design under test: the handler decides and stores under the lock, COMMITS (the lock and the
transaction end), then pushes one user at a time. A ledger row per (satsang, user) is CLAIMED before the
push and COMPLETED after it. A claim that is never completed belongs to a dead worker, and a later run
takes it over once `CLAIM_LEASE` has passed. So:

- a crash costs at most ONE duplicate push (the user in flight), never a missing reminder, never a
  resend to everybody;
- two workers (a duplicate job) never push the same user twice;
- no row lock and no open transaction is held while a push is in flight.

For THIS product a duplicate "starts in 30 minutes" is the cheaper mistake than a missing one: the
reminder is how an elder who is not looking at the app finds out, and a missed one means a missed
discourse. A process that DIES is simulated by raising `Crash`, a BaseException nothing in the code
catches, out of a push, and then dropping the session without committing, as a killed process would.
"""

# ruff: noqa: F811

import socket
import threading
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import app.push as push_module
import app.satsang_reminder as reminder
from app.config import get_settings
from app.db.satsang import SatsangReminderDelivery, SatsangSession
from tests.test_satsang_reminder_delivery import (  # noqa: F401 (fixtures are used by name)
    LEAD,
    _member,
    _row,
    _schedule,
    admin,
    clock,
    swami,
)


class Crash(BaseException):
    """The process dies here. A BaseException: no `except Exception` in the code can swallow it."""


class Pushes:
    """A stand-in for send_push that can die before the push leaves, after it, or fail."""

    def __init__(self):
        self.calls: list[uuid.UUID] = []
        self.crash_before: uuid.UUID | None = None
        self.crash_after: uuid.UUID | None = None
        self.fail_for: uuid.UUID | None = None
        self.on_call = None

    def __call__(self, session, *, user_id, title, body):
        if self.on_call:
            self.on_call(user_id)
        if user_id == self.crash_before:
            raise Crash
        if user_id == self.fail_for:
            raise RuntimeError("the push exploded")
        self.calls.append(user_id)
        if user_id == self.crash_after:
            raise Crash

    def count(self, user_id) -> int:
        return self.calls.count(user_id)


@pytest.fixture
def pushes(monkeypatch):
    fake = Pushes()
    monkeypatch.setattr(reminder, "send_push", fake)
    return fake


def _setup(client, db_session, admin, swami, clock, n=4):
    """n subscribed members whose language the render service cannot do (so no AI call is made), and a
    satsang whose reminder is due. Returns the satsang id and the recipients in the order the handler
    visits them."""
    for i in range(n):
        _member(db_session, f"M{i}", "ta")
    made = _schedule(client, admin, swami)
    row = _row(db_session, made.session.id)
    clock.at(row.scheduled_start_at - LEAD)
    order = [u.id for u in reminder._recipients(db_session, clock.current)]
    assert len(order) == n
    return made.session.id, order


def _run(engine, session_id):
    """One run of the handler in its own session, as the worker does: committed at the end, and
    dropped (the same as a death) if it raises."""
    with Session(engine) as own:
        reminder.handle_satsang_reminder(own, {"session_id": str(session_id)})
        own.commit()


def _after_the_lease(clock):
    clock.at(clock.current + reminder.CLAIM_LEASE + timedelta(seconds=1))


def _ledger(db_session, session_id):
    db_session.expire_all()
    rows = db_session.scalars(
        select(SatsangReminderDelivery).where(
            SatsangReminderDelivery.session_id == uuid.UUID(str(session_id))
        )
    )
    return {r.user_id: r for r in rows}


# --- 1. nothing is held while a push is in flight ---------------------------------------------------


def test_the_satsang_row_is_not_locked_while_a_push_is_in_flight(
    client, db_session, admin, swami, clock, pushes, engine
):
    """The row used to stay locked, FOR UPDATE, through every push: anything that wanted to end, cancel or
    move the satsang waited for the slowest push service."""
    sid, _order = _setup(client, db_session, admin, swami, clock)
    outcome = {}

    def probe(user_id):
        if "tried" in outcome:
            return
        outcome["tried"] = True
        with engine.connect() as other:
            try:
                other.execute(
                    text("select 1 from sessions where id = :i for update nowait"), {"i": sid}
                )
                outcome["row_lock_free"] = True
            except Exception as exc:  # noqa: BLE001 - LockNotAvailable is what the old code raised
                outcome["row_lock_free"] = False
                outcome["why"] = type(exc).__name__

    pushes.on_call = probe
    _run(engine, sid)
    assert outcome == {"tried": True, "row_lock_free": True}, outcome


def test_no_transaction_is_left_open_across_a_push(
    client, db_session, admin, swami, clock, engine, monkeypatch
):
    """Postgres kills a connection that sits idle in a transaction for 30 s (app/db/base.py), so the
    session used for the pushes must not be inside one when a push starts."""
    sid, _order = _setup(client, db_session, admin, swami, clock, n=2)
    seen = []
    with Session(engine) as own:

        def fake(session, *, user_id, title, body):
            seen.append(own.in_transaction())

        monkeypatch.setattr(reminder, "send_push", fake)
        reminder.handle_satsang_reminder(own, {"session_id": str(sid)})
        own.commit()
    assert seen == [False, False]


# --- 2. the process dies at the worst moment ---------------------------------------------------------


def test_a_death_after_a_push_left_but_before_it_was_recorded_costs_one_duplicate_not_a_resend_to_all(
    client, db_session, admin, swami, clock, pushes, engine
):
    sid, order = _setup(client, db_session, admin, swami, clock)
    pushes.crash_after = order[
        2
    ]  # the third user's push goes out, and the process dies before saying so
    with pytest.raises(Crash):
        _run(engine, sid)
    assert pushes.calls == order[:3]
    assert _row(db_session, sid).reminder_sent_at is None

    pushes.crash_after = None
    _after_the_lease(clock)  # the retry comes later; the dead worker's claim has expired
    _run(engine, sid)

    assert [pushes.count(u) for u in order] == [
        1,
        1,
        2,
        1,
    ]  # exactly one duplicate, the one in flight
    assert _row(db_session, sid).reminder_sent_at is not None
    _run(engine, sid)
    assert [pushes.count(u) for u in order] == [1, 1, 2, 1]  # and then it is over


def test_a_death_after_the_claim_but_before_the_push_still_gets_that_user_their_reminder(
    client, db_session, admin, swami, clock, pushes, engine
):
    sid, order = _setup(client, db_session, admin, swami, clock)
    pushes.crash_before = order[1]  # claimed, and dead before the push left: nobody was told
    with pytest.raises(Crash):
        _run(engine, sid)
    assert pushes.calls == order[:1]

    pushes.crash_before = None
    _after_the_lease(clock)
    _run(engine, sid)
    assert [pushes.count(u) for u in order] == [1, 1, 1, 1]  # nobody missed, nobody told twice
    assert _row(db_session, sid).reminder_sent_at is not None


def test_a_death_after_every_push_but_before_the_timestamp_pushes_nothing_again(
    client, db_session, admin, swami, clock, pushes, engine, monkeypatch
):
    sid, order = _setup(client, db_session, admin, swami, clock)
    real_finish = reminder._finish
    monkeypatch.setattr(reminder, "_finish", lambda *a, **k: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        _run(engine, sid)
    assert pushes.calls == order and _row(db_session, sid).reminder_sent_at is None

    monkeypatch.setattr(reminder, "_finish", real_finish)
    _run(engine, sid)
    assert pushes.calls == order  # the ledger says everybody has it: not one push more
    assert _row(db_session, sid).reminder_sent_at is not None


def test_a_run_straight_after_a_death_leaves_the_dead_workers_user_for_later_and_says_so(
    client, db_session, admin, swami, clock, pushes, engine
):
    """Before the claim has expired it cannot be told from a live worker's: the user is left alone, the
    others are served, and the job is NOT completed (a retry will come), so nobody is skipped for good."""
    sid, order = _setup(client, db_session, admin, swami, clock)
    pushes.crash_after = order[1]
    with pytest.raises(Crash):
        _run(engine, sid)
    pushes.crash_after = None

    with pytest.raises(reminder.ReminderNotFinished):
        _run(engine, sid)  # no time has passed: the claim on order[1] still looks alive
    assert [pushes.count(u) for u in order] == [
        1,
        1,
        1,
        1,
    ]  # the rest were served; order[1] untouched
    assert _row(db_session, sid).reminder_sent_at is None

    _after_the_lease(clock)
    _run(engine, sid)
    assert [pushes.count(u) for u in order] == [1, 2, 1, 1]
    assert _row(db_session, sid).reminder_sent_at is not None


# --- 3. two workers, and a push that fails ----------------------------------------------------------


def test_two_workers_at_once_never_push_the_same_user_twice(
    client, db_session, admin, swami, clock, pushes, engine
):
    """Two jobs for one satsang can both be due (a duplicate, or a reschedule route that also enqueued
    one). The second finds the first's user claimed, serves the others, and leaves."""
    sid, order = _setup(client, db_session, admin, swami, clock)
    inside, release = threading.Event(), threading.Event()
    held = {"done": False}

    def hold_the_first(user_id):
        if user_id == order[0] and not held["done"]:
            held["done"] = True
            inside.set()
            release.wait(15)

    pushes.on_call = hold_the_first
    first_result = {}

    def first():
        try:
            _run(engine, sid)
        except BaseException as exc:  # noqa: BLE001
            first_result["exc"] = exc

    t = threading.Thread(target=first)
    t.start()
    assert inside.wait(10), "the first worker never reached its first push"

    with pytest.raises(reminder.ReminderNotFinished):
        _run(engine, sid)  # the second worker, while the first is mid-push to order[0]
    assert pushes.count(order[0]) == 0  # the first user's push is still in flight: not repeated
    release.set()
    t.join(15)
    assert "exc" not in first_result, first_result
    assert [pushes.count(u) for u in order] == [1, 1, 1, 1]
    assert _row(db_session, sid).reminder_sent_at is not None


def test_a_push_that_blows_up_does_not_stop_the_others_and_is_tried_again_later(
    client, db_session, admin, swami, clock, pushes, engine
):
    sid, order = _setup(client, db_session, admin, swami, clock)
    pushes.fail_for = order[1]
    with pytest.raises(reminder.ReminderNotFinished):
        _run(engine, sid)
    assert [pushes.count(u) for u in order] == [1, 0, 1, 1]  # the user after the bad one was served
    assert _row(db_session, sid).reminder_sent_at is None

    pushes.fail_for = None
    _after_the_lease(clock)
    _run(engine, sid)
    assert [pushes.count(u) for u in order] == [1, 1, 1, 1]
    assert _row(db_session, sid).reminder_sent_at is not None


def test_a_satsang_cancelled_during_the_pushes_stops_the_rest(
    client, db_session, admin, swami, clock, pushes, engine
):
    sid, order = _setup(client, db_session, admin, swami, clock)

    def cancel_after_the_first(user_id):
        if user_id == order[1]:
            with engine.begin() as c:
                # fail fast if the handler still holds the row (the old code did: this used to hang)
                c.execute(text("set local lock_timeout = '2s'"))
                c.execute(text("update sessions set status='cancelled' where id=:i"), {"i": sid})

    pushes.on_call = cancel_after_the_first
    _run(engine, sid)
    # the push that was already in flight when it was cancelled completes; nobody after it is told
    assert pushes.calls == order[:2]


# --- 4. a push call can no longer hang forever -------------------------------------------------------


def test_the_push_call_is_given_a_timeout(db_session, monkeypatch):
    user = _member(db_session, "Timeout", "ta")
    seen = {}

    def fake_webpush(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(push_module, "webpush", fake_webpush)
    push_module.send_push(db_session, user_id=user.id, title="t", body="b")
    assert seen.get("timeout"), (
        "webpush was called without a timeout: a dead endpoint blocks for ever"
    )


def test_a_push_service_that_accepts_the_connection_and_never_answers_cannot_hold_the_call(
    monkeypatch,
):
    """The real webpush over a real socket, nothing mocked but the key and the subscription lookup (no
    database is touched: a stuck call must not hold a transaction open for the test's teardown to
    wait on). The call used to block for ever."""
    import base64
    from types import SimpleNamespace

    from cryptography.hazmat.primitives import serialization as ser
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    key = (
        base64.urlsafe_b64encode(
            vapid.private_key.private_bytes(
                ser.Encoding.DER, ser.PrivateFormat.PKCS8, ser.NoEncryption()
            )
        )
        .decode()
        .rstrip("=")
    )
    monkeypatch.setattr(get_settings(), "VAPID_PRIVATE_KEY", key)
    monkeypatch.setattr(push_module, "PUSH_TIMEOUT_S", (1.0, 1.0), raising=False)

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    held = []
    stop = threading.Event()

    def accept_and_say_nothing():
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                held.append(server.accept()[0])
            except OSError:
                pass

    threading.Thread(target=accept_and_say_nothing, daemon=True).start()
    subscription = SimpleNamespace(
        endpoint=f"http://127.0.0.1:{server.getsockname()[1]}/never",
        p256dh="BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        auth="tBHItJI5svbpez7KI4CCXg",
    )
    monkeypatch.setattr(
        push_module, "list_push_subscriptions_for_user", lambda s, user_id: [subscription]
    )
    monkeypatch.setattr(push_module, "delete_push_subscription", lambda *a, **k: None)

    finished = threading.Event()

    def call():
        push_module.send_push(None, user_id=uuid.uuid4(), title="t", body="b")
        finished.set()

    threading.Thread(target=call, daemon=True).start()
    began = time.monotonic()
    ok = finished.wait(8)
    stop.set()
    server.close()
    assert ok, "send_push was still blocked after 8 s on an endpoint that never answers"
    assert time.monotonic() - began < 6


def test_the_ledger_row_goes_with_its_satsang_and_with_its_user(
    client, db_session, admin, swami, clock, pushes, engine
):
    sid, order = _setup(client, db_session, admin, swami, clock, n=2)
    _run(engine, sid)
    assert set(_ledger(db_session, sid)) == set(order)
    db_session.execute(text("delete from users where id = :u"), {"u": order[0]})
    db_session.commit()
    assert set(_ledger(db_session, sid)) == {order[1]}
    db_session.execute(text("delete from sessions where id = :i"), {"i": sid})
    db_session.commit()
    assert _ledger(db_session, sid) == {}
    assert db_session.get(SatsangSession, uuid.UUID(str(sid))) is None
