"""Tests for app/push.py — Week 3 Phase 7: Web Push scaffolding.

`is_quiet_hours` is a pure function (no DB, no network) tested directly.
`send_push` touches the database (subscription rows) but never the real
network — `pywebpush.webpush` is monkeypatched on `app.push`'s own module
namespace (the name as imported there, not `pywebpush`'s copy — same
reasoning tests/conftest.py's `ws_login_as` and tests/test_health.py's
`check_database_connection` patches document: whichever module already
imported the name is what actually resolves at call time).

Written before app/push.py exists — collecting this file is expected to
fail (ModuleNotFoundError) until Step 2's implementation lands.
"""

from datetime import UTC, datetime, time
from types import SimpleNamespace

from app.push import is_quiet_hours, send_push
from sqlalchemy import select

from app.db.models import PushSubscription
from app.db.models import User as DbUser
from app.db.repository import upsert_push_subscription


def _make_user(db_session, name="User", preferred_language="en"):
    user = DbUser(name=name, preferred_language=preferred_language)
    db_session.add(user)
    db_session.flush()
    return user


def _utc(hour, minute=0):
    return datetime(2026, 8, 20, hour, minute, tzinfo=UTC)


# --- is_quiet_hours -----------------------------------------------------


def test_is_quiet_hours_true_inside_a_simple_daytime_window():
    # Asia/Kolkata is a fixed UTC+5:30 offset, no DST. 10:00 UTC = 15:30
    # IST — solidly inside a 09:00-17:00 window.
    assert (
        is_quiet_hours(
            timezone="Asia/Kolkata",
            quiet_hours_start=time(9, 0),
            quiet_hours_end=time(17, 0),
            now=_utc(10, 0),
        )
        is True
    )


def test_is_quiet_hours_false_outside_a_simple_daytime_window():
    # 20:00 UTC = 01:30 IST the next day — solidly outside 09:00-17:00.
    assert (
        is_quiet_hours(
            timezone="Asia/Kolkata",
            quiet_hours_start=time(9, 0),
            quiet_hours_end=time(17, 0),
            now=_utc(20, 0),
        )
        is False
    )


def test_is_quiet_hours_overnight_wraparound_true_late_at_night():
    # A window that spans midnight, 20:00-07:00. 21:00 UTC = 02:30 IST
    # (next day) — solidly inside.
    assert (
        is_quiet_hours(
            timezone="Asia/Kolkata",
            quiet_hours_start=time(20, 0),
            quiet_hours_end=time(7, 0),
            now=_utc(21, 0),
        )
        is True
    )


def test_is_quiet_hours_overnight_wraparound_false_during_the_day():
    # 06:30 UTC = 12:00 IST (noon) — solidly outside a 20:00-07:00 window,
    # the case a naive "start <= now <= end" comparison (without handling
    # wraparound) would get backwards.
    assert (
        is_quiet_hours(
            timezone="Asia/Kolkata",
            quiet_hours_start=time(20, 0),
            quiet_hours_end=time(7, 0),
            now=_utc(6, 30),
        )
        is False
    )


def test_is_quiet_hours_false_when_timezone_is_unset():
    # No timezone means quiet hours can't be correctly computed in this
    # user's local time — never block push for a user in this state
    # (Step 0 answer B), rather than silently assuming UTC.
    assert (
        is_quiet_hours(
            timezone=None,
            quiet_hours_start=time(20, 0),
            quiet_hours_end=time(7, 0),
            now=_utc(23, 0),  # would read as "quiet" in nearly any zone
        )
        is False
    )


def test_is_quiet_hours_falls_back_to_default_window_when_unset():
    # A user with a timezone but no explicit quiet_hours_start/end falls
    # back to the confirmed default window (20:00-07:00), not "quiet hours
    # never enforced."
    assert (
        is_quiet_hours(
            timezone="Asia/Kolkata",
            quiet_hours_start=None,
            quiet_hours_end=None,
            now=_utc(21, 0),  # 02:30 IST — inside the default window
        )
        is True
    )
    assert (
        is_quiet_hours(
            timezone="Asia/Kolkata",
            quiet_hours_start=None,
            quiet_hours_end=None,
            now=_utc(6, 30),  # 12:00 IST — outside the default window
        )
        is False
    )


# --- send_push ------------------------------------------------------------


def test_send_push_calls_webpush_with_right_payload_and_keys(db_session, monkeypatch):
    import app.push as push_module

    user = _make_user(db_session, "Alice")
    subscription = upsert_push_subscription(
        db_session,
        user_id=user.id,
        endpoint="https://fcm.googleapis.com/fcm/send/abc123",
        p256dh="p256dh-value",
        auth="auth-value",
    )

    calls = []

    def fake_webpush(subscription_info, data=None, **kwargs):
        calls.append({"subscription_info": subscription_info, "data": data})

    monkeypatch.setattr(push_module, "webpush", fake_webpush)

    send_push(db_session, user_id=user.id, title="Hello", body="World")

    assert len(calls) == 1
    call = calls[0]
    assert call["subscription_info"]["endpoint"] == subscription.endpoint
    assert call["subscription_info"]["keys"]["p256dh"] == "p256dh-value"
    assert call["subscription_info"]["keys"]["auth"] == "auth-value"
    # The payload carries this call's own title/body, not hardcoded copy.
    assert "Hello" in call["data"]
    assert "World" in call["data"]

    # A successful send is not a reason to touch the subscription row.
    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.id == subscription.id))
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_send_push_deletes_subscription_on_404(db_session, monkeypatch):
    import app.push as push_module
    from pywebpush import WebPushException

    user = _make_user(db_session, "Alice")
    subscription = upsert_push_subscription(
        db_session, user_id=user.id, endpoint="https://a.example/1", p256dh="p", auth="a"
    )

    def fake_webpush(subscription_info, data=None, **kwargs):
        raise WebPushException("Not Found", response=SimpleNamespace(status_code=404))

    monkeypatch.setattr(push_module, "webpush", fake_webpush)

    send_push(db_session, user_id=user.id, title="Hi", body="There")

    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.id == subscription.id))
        .scalars()
        .all()
    )
    assert rows == []


def test_send_push_deletes_subscription_on_410(db_session, monkeypatch):
    import app.push as push_module
    from pywebpush import WebPushException

    user = _make_user(db_session, "Alice")
    subscription = upsert_push_subscription(
        db_session, user_id=user.id, endpoint="https://a.example/1", p256dh="p", auth="a"
    )

    def fake_webpush(subscription_info, data=None, **kwargs):
        raise WebPushException("Gone", response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(push_module, "webpush", fake_webpush)

    send_push(db_session, user_id=user.id, title="Hi", body="There")

    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.id == subscription.id))
        .scalars()
        .all()
    )
    assert rows == []


def test_send_push_non_gone_failure_does_not_delete_or_raise(db_session, monkeypatch):
    import app.push as push_module
    from pywebpush import WebPushException

    user = _make_user(db_session, "Alice")
    subscription = upsert_push_subscription(
        db_session, user_id=user.id, endpoint="https://a.example/1", p256dh="p", auth="a"
    )

    def fake_webpush(subscription_info, data=None, **kwargs):
        raise WebPushException("Too Many Requests", response=SimpleNamespace(status_code=429))

    monkeypatch.setattr(push_module, "webpush", fake_webpush)

    # Must not propagate — Phase 5's per-socket try/except precedent,
    # applied here: one bad delivery can't crash the caller.
    send_push(db_session, user_id=user.id, title="Hi", body="There")

    rows = (
        db_session.execute(select(PushSubscription).where(PushSubscription.id == subscription.id))
        .scalars()
        .all()
    )
    assert len(rows) == 1


def test_send_push_one_dead_subscription_does_not_block_the_users_other_devices(
    db_session, monkeypatch
):
    import app.push as push_module
    from pywebpush import WebPushException

    user = _make_user(db_session, "Alice")
    dead = upsert_push_subscription(
        db_session, user_id=user.id, endpoint="https://a.example/dead", p256dh="p1", auth="a1"
    )
    alive = upsert_push_subscription(
        db_session, user_id=user.id, endpoint="https://a.example/alive", p256dh="p2", auth="a2"
    )

    delivered_to = []

    def fake_webpush(subscription_info, data=None, **kwargs):
        if subscription_info["endpoint"] == dead.endpoint:
            raise WebPushException("Gone", response=SimpleNamespace(status_code=410))
        delivered_to.append(subscription_info["endpoint"])

    monkeypatch.setattr(push_module, "webpush", fake_webpush)

    send_push(db_session, user_id=user.id, title="Hi", body="There")

    assert delivered_to == [alive.endpoint]
    remaining = (
        db_session.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))
        .scalars()
        .all()
    )
    assert {s.endpoint for s in remaining} == {alive.endpoint}
