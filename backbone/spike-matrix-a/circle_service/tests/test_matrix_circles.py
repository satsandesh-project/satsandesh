"""Week 4: the same four required behaviours as Week 3's
backbone/spike-custom-lite/tests/test_circles.py, run against a real
Tuwunel instance -- plus one extra test for a real, discovered difference
from the outbox model (see below).

Tests the store directly rather than going through FastAPI's TestClient.
Week 3's tests used TestClient because they needed app.py's lifespan to
start a background dispatcher task and to exercise a live websocket --
neither exists here (Matrix has no push mechanism in this interface;
delivery is pull-based via list_messages, same as the rest of the store).
Going through the app would only add an HTTP hop and a bootstrap-on-every-
test-run cost for no additional coverage; app.py's routing itself is
covered separately in gateway/tests/test_circles.py's contract test and
by the fact that gateway/backbone_client.py's shapes match app.py's
routes exactly (checked by hand, matching custom-lite's precedent).

Appservice bootstrap happens once per test session (see the
`matrix_store` fixture) -- it's the same idempotent registration on every
call regardless, but there's no reason to pay the ~1s confirmation-poll
cost per test.
"""

import uuid

import pytest
import pytest_asyncio

from bootstrap import bootstrap_appservice
from matrix_circle_store import MatrixCircleStore
from app import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    AS_ID,
    AS_TOKEN,
    BOT_LOCALPART,
    HOMESERVER_URL,
    SERVER_NAME,
    _render_registration_yaml,
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def matrix_store():
    await bootstrap_appservice(
        homeserver_url=HOMESERVER_URL,
        registration_yaml=_render_registration_yaml(),
        appservice_id=AS_ID,
        admin_username=ADMIN_USERNAME,
        admin_password=ADMIN_PASSWORD,
    )
    return MatrixCircleStore(
        homeserver_url=HOMESERVER_URL,
        as_token=AS_TOKEN,
        server_name=SERVER_NAME,
        bot_localpart=BOT_LOCALPART,
    )


def _tag() -> str:
    # Fresh names per test rather than resetting server state between
    # tests -- Tuwunel has no equivalent of Postgres's TRUNCATE, and
    # unique names avoid cross-test interference just as well.
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio(loop_scope="session")
async def test_announcement_reaches_all_three_members(matrix_store):
    t = _tag()
    circle_id = await matrix_store.create_circle(f"Circle {t}")
    members = [f"bob_{t}", f"carol_{t}", f"dave_{t}"]
    for m in members:
        await matrix_store.add_member(circle_id, m)

    assert await matrix_store.list_members(circle_id) == sorted(members)

    message_id = await matrix_store.post_announcement(circle_id, "alice", "satsang at 6pm")
    assert message_id

    messages = await matrix_store.list_messages(circle_id)
    assert len(messages) == 1
    assert messages[0].body == "satsang at 6pm"
    assert messages[0].sender_id == "alice"
    assert messages[0].circle_id == circle_id
    # "alice" was never added as a member -- confirms sender need not be
    # a member (interface contract) actually works against a real server,
    # not just in the interface's own docstring.
    assert "alice" not in await matrix_store.list_members(circle_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_non_member_does_not_receive(matrix_store):
    t = _tag()
    circle_id = await matrix_store.create_circle(f"Circle {t}")
    await matrix_store.add_member(circle_id, f"bob_{t}")
    await matrix_store.post_announcement(circle_id, "alice", "members only")

    members = await matrix_store.list_members(circle_id)
    assert members == [f"bob_{t}"]
    assert f"mallory_{t}" not in members


@pytest.mark.asyncio(loop_scope="session")
async def test_removed_member_stops_appearing_but_history_is_kept(matrix_store):
    """Step 4.4 equivalent. "Stops receiving future announcements" for
    this interface means: no longer in list_members, so no future
    add_member-scoped delivery targets them. "Keeps what was already
    delivered" means: a message sent while they were a member is still
    in list_messages afterward -- kicking doesn't rewrite room history."""
    t = _tag()
    circle_id = await matrix_store.create_circle(f"Circle {t}")
    larry = f"larry_{t}"
    await matrix_store.add_member(circle_id, larry)

    await matrix_store.post_announcement(circle_id, "alice", "before removal")
    await matrix_store.remove_member(circle_id, larry)
    assert larry not in await matrix_store.list_members(circle_id)

    await matrix_store.post_announcement(circle_id, "alice", "after removal")

    bodies = [m.body for m in await matrix_store.list_messages(circle_id)]
    assert "before removal" in bodies
    assert "after removal" in bodies
    # Removing larry didn't erase the message sent while he was present.


@pytest.mark.asyncio(loop_scope="session")
async def test_matrix_history_visibility_differs_from_outbox_model(matrix_store):
    """REAL FINDING, not the same guarantee as Week 3's outbox test of
    the same name.

    Week 3's offline-member test proved the outbox redelivers a message
    to a member who was offline *when they were already a member*. There
    is no equivalent "offline" state to test here -- list_messages is
    pull-based and always returns full history to a current member,
    there's no push/retry queue to observe.

    The genuinely different, verified-on-a-real-server behaviour is
    this: a member added to the circle AFTER a message was posted --
    who was never a member, invited, or present at post time -- can
    still see that earlier message. Verified directly against Tuwunel
    with a real per-user impersonated read, not just the bot's own
    system-level list_messages (which would trivially show it regardless
    of any individual member's join time). This is Tuwunel's default
    room history_visibility ("shared"): visible to any CURRENT member
    regardless of when they joined.

    interfaces.py's own documented contract for the outbox
    implementation is the opposite: "A member added a second later does
    not receive it." Both are correct for their own backbone -- this is
    exactly the kind of behavioural difference ADR 0002 needs to weigh,
    not paper over with "circles work the same either way."
    """
    from matrix_circle_store import _to_mxid
    from matrix_client import MatrixClient

    t = _tag()
    circle_id = await matrix_store.create_circle(f"Circle {t}")

    await matrix_store.post_announcement(circle_id, "alice", f"pre-join-{t}")

    late_joiner = f"latecomer_{t}"
    await matrix_store.add_member(circle_id, late_joiner)

    # Read AS the late joiner's own impersonated access -- not the bot's
    # system-level read -- to test what THAT USER can actually see,
    # which is the thing "does an offline/late member get it" is really
    # asking on a real Matrix server.
    client = MatrixClient(matrix_store._client._base, matrix_store._client._as_token, "localhost")
    mxid = _to_mxid(late_joiner, "localhost")
    events = await client.messages(circle_id, as_user=mxid, limit=10)
    bodies = [e["content"].get("body") for e in events if e["type"] == "m.room.message"]

    assert f"pre-join-{t}" in bodies, (
        "expected the late joiner to see the pre-join message under "
        "Tuwunel's default history_visibility=shared -- if this fails, "
        "the default changed and docs/adr/0002-chat-backbone.md's finding "
        "needs updating, not this assertion weakening"
    )
