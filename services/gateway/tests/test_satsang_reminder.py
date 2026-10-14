"""The satsang reminder: "starting in 30 minutes", on the job queue's clock (app/satsang_reminder.py).

The reminder is one `satsang_reminder` job enqueued when a satsang is scheduled, with
`jobs.next_attempt_at = scheduled_start_at - 30 min`. The queue already claims a job only once that
time has passed and already retries with backoff and dead-letters, so none of that is reimplemented
here; these tests pin the five things that are this feature's own:

1. exactly ONE job per satsang, due at the right moment, and not claimed before it;
2. the handler acts on the satsang AS IT IS NOW (cancelled, started, moved), not as it was when the
   job was made;
3. `reminder_sent_at` and the job's completion commit together, so a second run never sends twice;
4. a failure retries with the queue's backoff and ends dead after JOB_MAX_ATTEMPTS, not forever;
5. who is pushed, and when (quiet hours).

Time is injected, never slept: two clocks are swapped for one the test controls, the repository's
(what `claim_next_job` and `fail_job` read) and the reminder module's own `_now`.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from contracts.chat.satsang import SatsangSessionCreated
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.jobs as jobs_module
import app.satsang_reminder as reminder
from app.config import get_settings
from app.db import repository
from app.db.models import Job, PushSubscription
from app.db.models import User as DbUser
from app.db.repository import claim_next_job, compute_backoff_seconds, enqueue_job
from app.db.satsang import SatsangSession
from app.tokens import issue_token

LEAD = timedelta(minutes=30)


class Clock:
    """One controllable now(), installed in both places the code reads the time."""

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
def sent(monkeypatch):
    """Replaces the delivery with a recorder: (session title, minutes left, reminder_sent_at seen)."""
    calls: list[tuple[str, int, object]] = []

    def record(session, row, minutes_left, *_rendered):
        calls.append((row.title, minutes_left, row.reminder_sent_at))

    monkeypatch.setattr(reminder, "deliver", record)
    return calls


def _user(db_session, name, role="elder", **extra):
    user = DbUser(name=name, preferred_language="te", role=role, **extra)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def admin(db_session):
    return _user(db_session, "Asha", "admin")


@pytest.fixture
def swami(db_session):
    return _user(db_session, "Swami Ananda")


def _schedule(client, who, speaker, *, minutes=120, title="Thursday discourse"):
    resp = client.post(
        "/sessions",
        json={
            "title": title,
            "speaker_user_id": str(speaker.id),
            "scheduled_start_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(),
        },
        headers={"Authorization": f"Bearer {issue_token(who.id)}"},
    )
    assert resp.status_code == 201, resp.text
    return SatsangSessionCreated.model_validate(resp.json())


def _jobs(db_session):
    db_session.expire_all()
    return list(db_session.scalars(select(Job).where(Job.job_type == reminder.JOB_TYPE)))


def _row(db_session, session_id) -> SatsangSession:
    db_session.expire_all()
    return db_session.get(SatsangSession, uuid.UUID(session_id))


def _scheduled(client, db_session, admin, swami, **kw):
    made = _schedule(client, admin, swami, **kw)
    return made, _row(db_session, made.session.id)


def _run_handler(db_session, session_id):
    reminder.handle_satsang_reminder(db_session, {"session_id": str(session_id)})
    db_session.flush()


# --- 1. one job, due at the right moment, not claimed early --------------------------------------


def test_scheduling_a_satsang_enqueues_exactly_one_reminder_due_thirty_minutes_before(
    client, db_session, admin, swami
):
    made, row = _scheduled(client, db_session, admin, swami, minutes=120)
    (job,) = _jobs(db_session)
    assert job.payload == {"session_id": made.session.id}
    assert job.next_attempt_at == row.scheduled_start_at - LEAD
    assert job.status == "queued"
    assert job.max_attempts == get_settings().JOB_MAX_ATTEMPTS


def test_each_satsang_gets_its_own_single_reminder(client, db_session, admin, swami):
    a = _schedule(client, admin, swami, title="One")
    b = _schedule(client, admin, swami, title="Two", minutes=300)
    ids = sorted(j.payload["session_id"] for j in _jobs(db_session))
    assert ids == sorted([a.session.id, b.session.id])


def test_nothing_after_scheduling_adds_a_second_reminder(client, db_session, admin, swami):
    made = _schedule(client, admin, swami)
    headers = {"Authorization": f"Bearer {issue_token(admin.id)}"}
    assert client.post(f"/sessions/{made.session.id}/key", headers=headers).status_code == 200
    assert client.get("/sessions/manage", headers=headers).status_code == 200
    assert client.post(f"/sessions/{made.session.id}/cancel", headers=headers).status_code == 200
    assert len(_jobs(db_session)) == 1


def test_a_satsang_that_fails_to_save_leaves_no_reminder_behind(client, db_session, admin, swami):
    resp = client.post(
        "/sessions",
        json={
            "title": "In the past",
            "speaker_user_id": str(swami.id),
            "scheduled_start_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {issue_token(admin.id)}"},
    )
    assert resp.status_code == 422
    assert _jobs(db_session) == []


def test_the_reminder_is_not_claimed_before_it_is_due(client, db_session, admin, swami, clock):
    _, row = _scheduled(client, db_session, admin, swami, minutes=120)
    due = row.scheduled_start_at - LEAD
    for early in (timedelta(hours=1), timedelta(minutes=1), timedelta(seconds=1)):
        clock.at(due - early)
        assert claim_next_job(db_session, worker_id="w") is None, f"claimed {early} early"


def test_the_reminder_is_claimed_once_it_is_due(client, db_session, admin, swami, clock):
    made, row = _scheduled(client, db_session, admin, swami, minutes=120)
    clock.at(row.scheduled_start_at - LEAD)
    claimed = claim_next_job(db_session, worker_id="w")
    assert claimed is not None and claimed.payload == {"session_id": made.session.id}


# --- 2. the handler acts on the satsang as it is NOW ----------------------------------------------


def test_the_handler_sends_once_when_the_satsang_is_scheduled_and_ahead(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert sent == [("Thursday discourse", 30, None)]
    assert _row(db_session, made.session.id).reminder_sent_at is not None


def test_a_reminder_claimed_late_says_how_long_is_really_left(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - timedelta(minutes=12, seconds=30))
    _run_handler(db_session, made.session.id)
    assert [minutes for _, minutes, _ in sent] == [13]  # rounded up, never "0 minutes"


def test_a_cancelled_satsang_sends_nothing(client, db_session, admin, swami, clock, sent):
    made, row = _scheduled(client, db_session, admin, swami)
    resp = client.post(
        f"/sessions/{made.session.id}/cancel",
        headers={"Authorization": f"Bearer {issue_token(admin.id)}"},
    )
    assert resp.status_code == 200
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert sent == []
    assert _row(db_session, made.session.id).reminder_sent_at is None


@pytest.mark.parametrize("status", ["live", "ended"])
def test_a_satsang_that_already_started_gets_no_starting_soon_reminder(
    client, db_session, admin, swami, clock, sent, status
):
    made, row = _scheduled(client, db_session, admin, swami)
    started = row.scheduled_start_at - timedelta(minutes=2)
    row.status, row.started_at = "live", started
    db_session.commit()
    if status == "ended":
        row.status, row.ended_at = "ended", started + timedelta(minutes=1)
        db_session.commit()
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert sent == []


def test_a_satsang_whose_start_has_passed_without_going_live_gets_none(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at + timedelta(minutes=5))
    _run_handler(db_session, made.session.id)
    assert sent == []


def test_a_missing_satsang_is_a_quiet_no_op_not_a_retry_loop(db_session, clock, sent):
    _run_handler(db_session, uuid.uuid4())
    assert sent == []


def test_a_payload_without_a_session_id_dies_at_once_it_cannot_ever_work(db_session):
    with pytest.raises(jobs_module.PermanentJobError):
        reminder.handle_satsang_reminder(db_session, {})
    with pytest.raises(jobs_module.PermanentJobError):
        reminder.handle_satsang_reminder(db_session, {"session_id": "not-a-uuid"})


# --- 2b. the satsang is moved after its reminder was enqueued ----------------------------------------
#
# Design (b), the handler re-reads the satsang's CURRENT start and acts on that, over (a), re-enqueue
# on edit and cancel the old job. The row is the single source of truth, so there is no old job to
# find and no orphan that could still fire; the cost is one wake-up when a satsang is moved LATER.
# Its one real limit is tested below too: a satsang moved earlier than its old reminder time is
# reminded at the OLD time (late), or not at all if it has started by then.


def test_a_satsang_moved_later_is_not_reminded_at_the_old_time(
    client, db_session, admin, swami, clock, sent
):
    """Through the real worker, so the original job really completes and what is left is only what
    the handler enqueued."""
    made, row = _scheduled(client, db_session, admin, swami, minutes=120)
    old_due = row.scheduled_start_at - LEAD
    row.scheduled_start_at = row.scheduled_start_at + timedelta(hours=3)
    db_session.commit()
    new_due = row.scheduled_start_at - LEAD

    clock.at(old_due)
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True
    assert sent == []  # nothing at the old time
    assert _row(db_session, made.session.id).reminder_sent_at is None
    jobs = _jobs(db_session)
    assert sorted(j.status for j in jobs) == [
        "done",
        "queued",
    ]  # the old job is over, one new one waits
    (waiting,) = [j for j in jobs if j.status == "queued"]
    assert waiting.next_attempt_at == new_due  # it woke once and asked again for the new time

    clock.at(new_due - timedelta(seconds=1))
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is False
    clock.at(new_due)
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True
    assert [minutes for _, minutes, _ in sent] == [30]
    assert sorted(j.status for j in _jobs(db_session)) == ["done", "done"]


def test_moving_it_later_and_back_does_not_leave_a_second_reminder_to_fire(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami, minutes=120)
    due = row.scheduled_start_at - LEAD
    clock.at(due)
    _run_handler(db_session, made.session.id)
    db_session.commit()
    _run_handler(db_session, made.session.id)  # a duplicate job, as a crash or an edit could cause
    assert len(sent) == 1


def test_a_satsang_moved_earlier_is_still_reminded_if_it_has_not_started_at_the_old_time(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami, minutes=120)
    old_due = row.scheduled_start_at - LEAD
    row.scheduled_start_at = row.scheduled_start_at - timedelta(minutes=20)
    db_session.commit()
    clock.at(old_due)
    _run_handler(db_session, made.session.id)
    assert [minutes for _, minutes, _ in sent] == [10]


def test_KNOWN_LIMIT_a_satsang_moved_earlier_than_its_old_reminder_time_gets_none(
    client, db_session, admin, swami, clock, sent
):
    """Written down so it is a decision, not a surprise: the job is not due until the old time, and
    by then the satsang has started. A reschedule route should also enqueue a fresh job (the
    handler is idempotent, so both are safe together)."""
    made, row = _scheduled(client, db_session, admin, swami, minutes=120)
    old_due = row.scheduled_start_at - LEAD
    row.scheduled_start_at = row.scheduled_start_at - timedelta(minutes=60)
    db_session.commit()
    clock.at(old_due)
    _run_handler(db_session, made.session.id)
    assert sent == []


# --- 3. reminder_sent_at and the send commit together; a second run never sends twice -------------


def test_a_second_run_of_the_handler_does_not_send_again(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    db_session.commit()
    _run_handler(db_session, made.session.id)
    db_session.commit()
    _run_handler(db_session, made.session.id)
    assert len(sent) == 1


def test_the_timestamp_is_not_set_until_the_send_has_returned(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert sent[0][2] is None  # when deliver() ran, reminder_sent_at was still empty


def test_a_send_that_fails_leaves_the_timestamp_empty_so_the_retry_sends(
    client, db_session, admin, swami, clock, monkeypatch
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)

    def boom(session, row, minutes_left, *_rendered):
        raise RuntimeError("push service down")

    monkeypatch.setattr(reminder, "deliver", boom)
    with pytest.raises(RuntimeError):
        _run_handler(db_session, made.session.id)
    db_session.rollback()  # what the worker does when a handler raises
    assert _row(db_session, made.session.id).reminder_sent_at is None

    calls = []
    monkeypatch.setattr(reminder, "deliver", lambda s, r, m, *_: calls.append(m))
    _run_handler(db_session, made.session.id)
    assert calls == [30]


def test_two_workers_running_the_same_reminder_at_once_send_it_once(
    client, db_session, admin, swami, clock, monkeypatch, engine
):
    """Two jobs for one satsang can both be due (a duplicate, or a reschedule route that also
    enqueued one). The row lock makes the second wait for the first to commit, then see
    `reminder_sent_at` and send nothing. Without the lock both read it empty and both send."""
    made, row = _scheduled(client, db_session, admin, swami)
    db_session.commit()
    clock.at(row.scheduled_start_at - LEAD)
    inside = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def slow_deliver(session, row_, minutes_left):
        calls.append(minutes_left)
        inside.set()
        release.wait(10)

    monkeypatch.setattr(reminder, "deliver", slow_deliver)

    def worker():
        with Session(engine) as own:
            reminder.handle_satsang_reminder(own, {"session_id": made.session.id})
            own.commit()

    first = threading.Thread(target=worker)
    first.start()
    assert inside.wait(5), "the first worker never reached the send"
    second = threading.Thread(target=worker)
    second.start()
    second.join(1.0)
    second_was_waiting = second.is_alive()  # holding at the row lock, not racing past it
    release.set()
    first.join(10)
    second.join(10)
    assert second_was_waiting
    assert calls == [30]
    assert _row(db_session, made.session.id).reminder_sent_at is not None


def test_through_the_real_worker_the_timestamp_and_the_completion_commit_together(
    client, db_session, admin, swami, clock, sent
):
    made, row = _scheduled(client, db_session, admin, swami)
    db_session.commit()
    clock.at(row.scheduled_start_at - LEAD)
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True
    (job,) = _jobs(db_session)
    assert job.status == "done"
    assert _row(db_session, made.session.id).reminder_sent_at is not None  # from another session
    assert len(sent) == 1
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is False  # nothing left to run


# --- 4. a failure retries with the queue's backoff and dies after JOB_MAX_ATTEMPTS ----------------


def test_a_failing_send_retries_with_backoff_and_dead_letters_after_max_attempts(
    client, db_session, admin, swami, clock, monkeypatch
):
    monkeypatch.setattr(get_settings(), "JOB_MAX_ATTEMPTS", 3)
    made, row = _scheduled(client, db_session, admin, swami)
    db_session.commit()

    def boom(session, row, minutes_left, *_rendered):
        raise RuntimeError("push service down")

    monkeypatch.setattr(reminder, "deliver", boom)
    due = row.scheduled_start_at - LEAD
    clock.at(due)

    for attempt in (1, 2):
        assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True
        (job,) = _jobs(db_session)
        assert (job.status, job.attempts) == ("queued", attempt)
        backoff = timedelta(seconds=compute_backoff_seconds(attempt))
        assert job.next_attempt_at == clock.current + backoff  # the queue's own backoff
        assert "push service down" in job.last_error
        assert jobs_module._run_one_claimed_job(this_worker_id="w1") is False  # not yet
        clock.at(clock.current + backoff)

    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True
    (job,) = _jobs(db_session)
    assert (job.status, job.attempts) == ("dead", 3)
    clock.at(clock.current + timedelta(hours=1))
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is False  # dead: never again
    assert _row(db_session, made.session.id).reminder_sent_at is None


def test_the_job_is_registered_with_the_worker_so_a_due_reminder_is_run_not_failed(
    client, db_session, admin, swami, clock, sent
):
    """If nothing registered the handler the worker would record 'no handler registered' and retry
    forever. app/main.py registers it, once, at import."""
    _, row = _scheduled(client, db_session, admin, swami)
    db_session.commit()
    clock.at(row.scheduled_start_at - LEAD)
    jobs_module._run_one_claimed_job(this_worker_id="w1")
    (job,) = _jobs(db_session)
    assert job.status == "done" and job.last_error is None


def test_other_job_types_are_unaffected(db_session):
    enqueue_job(db_session, job_type="noop", payload={})
    db_session.commit()
    assert jobs_module._run_one_claimed_job(this_worker_id="w1") is True


# --- 5. who is pushed, and when --------------------------------------------------------------------


@pytest.fixture
def pushes(monkeypatch):
    calls: list[tuple[uuid.UUID, str, str]] = []
    monkeypatch.setattr(
        reminder,
        "send_push",
        lambda session, *, user_id, title, body: calls.append((user_id, title, body)),
    )
    # who is pushed is this fixture's subject, not the translation: no AI call (see the delivery tests)
    monkeypatch.setattr(reminder, "render_for_languages", lambda text, languages: {})
    return calls


def _subscribe(db_session, user, n=1):
    for i in range(n):
        db_session.add(
            PushSubscription(
                user_id=user.id,
                endpoint=f"https://push.example/{uuid.uuid4()}",
                p256dh="k",
                auth="a",
            )
        )
    db_session.commit()


def test_every_user_with_a_push_subscription_is_pushed_once_however_many_devices(
    client, db_session, admin, swami, clock, pushes
):
    a, b, nobody = _user(db_session, "A"), _user(db_session, "B"), _user(db_session, "C")
    _subscribe(db_session, a, n=3)
    _subscribe(db_session, b)
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert sorted(str(uid) for uid, _, _ in pushes) == sorted([str(a.id), str(b.id)])
    assert nobody.id not in {uid for uid, _, _ in pushes}


def test_the_push_names_the_satsang_and_says_how_long_is_left(
    client, db_session, admin, swami, clock, pushes
):
    _subscribe(db_session, _user(db_session, "A"))
    made, row = _scheduled(client, db_session, admin, swami, title="Gita discourse")
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    ((_, title, body),) = pushes
    assert "Gita discourse" in title + body and "30" in body


def test_a_user_in_their_quiet_hours_is_not_pushed(client, db_session, admin, swami, clock, pushes):
    asleep = _user(db_session, "Asleep", timezone="UTC")
    awake = _user(db_session, "Awake", timezone="UTC")
    _subscribe(db_session, asleep)
    _subscribe(db_session, awake)
    made, row = _scheduled(client, db_session, admin, swami)
    fire = row.scheduled_start_at - LEAD
    clock.at(fire)
    local = fire.astimezone(UTC).time()
    asleep.quiet_hours_start = (datetime.combine(fire.date(), local) - timedelta(hours=1)).time()
    asleep.quiet_hours_end = (datetime.combine(fire.date(), local) + timedelta(hours=1)).time()
    awake.quiet_hours_start = (datetime.combine(fire.date(), local) + timedelta(hours=2)).time()
    awake.quiet_hours_end = (datetime.combine(fire.date(), local) + timedelta(hours=3)).time()
    db_session.commit()
    _run_handler(db_session, made.session.id)
    assert [uid for uid, _, _ in pushes] == [awake.id]


def test_a_user_with_no_timezone_is_never_silenced(client, db_session, admin, swami, clock, pushes):
    unknown = _user(db_session, "NoZone")
    _subscribe(db_session, unknown)
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert [uid for uid, _, _ in pushes] == [unknown.id]


def test_nobody_subscribed_is_still_a_completed_reminder_not_an_error(
    client, db_session, admin, swami, clock, pushes
):
    made, row = _scheduled(client, db_session, admin, swami)
    clock.at(row.scheduled_start_at - LEAD)
    _run_handler(db_session, made.session.id)
    assert pushes == []
    assert _row(db_session, made.session.id).reminder_sent_at is not None


# --- the shared-file edit in app/main.py is one import and one call -----------------------------------


def test_main_py_gained_only_the_import_and_the_register_handler_call():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf8")
    lines = [ln for ln in source.splitlines() if "satsang_reminder" in ln]
    assert len(lines) == 2, lines
    assert any(ln.startswith("from app import satsang_reminder") for ln in lines)
    assert sum("register_handler(" in ln for ln in lines) == 1
    # at module level, not inside the lifespan: register_handler raises on a second registration and
    # every TestClient context re-runs the lifespan
    call = next(ln for ln in lines if "register_handler(" in ln)
    assert not call.startswith((" ", "\t"))
