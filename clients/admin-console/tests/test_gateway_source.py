"""
GatewayQueueSource against the chat mock, and the failure modes a moderator
would actually hit.

The mock (contracts/chat/mock/, PR #105 and #106) serves the same four
routes as the gateway with the same rules, so these tests drive the real
request/response path -- paging, the 409 split, the role check -- without a
socket or a database. A FastAPI TestClient is an httpx.Client, which is
what `GatewayQueueSource(client=...)` accepts.

What the mock cannot show is spelled out in its own README and in #105: a
moderator is whoever sends the header, there is no WebSocket push, voice
notes are never queued. Anything that needs those stays on the fixture
backend, which is why that one is kept.

Review round on PR #103 (Kshitiz, Veerendra) asked for exactly this:
a test or two against the mock, a paging loop, and a message for every
failure that is not a review conflict.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from admin_console.source import (
    ConsoleConfigError,
    FixtureQueueSource,
    GatewayQueueSource,
    ReviewConflict,
    SourceError,
    build_source_from_env,
)
from fastapi.testclient import TestClient

import contracts.chat.mock.app as mock_app
from contracts.chat.moderation import ModerationReviewIn

MOD = {"X-Mock-Role": "moderator"}


@pytest.fixture
def mock_client():
    # The mock's state is module-level and the queue is global, so every test
    # starts empty -- the same arrangement as the mock's own tests.
    mock_app._messages.clear()
    mock_app._message_seq.clear()
    mock_app._events.clear()
    with TestClient(mock_app.app) as client:
        yield client


def _hold(client: TestClient, n: int = 1) -> list[str]:
    """Put `n` messages into the mock's queue (its keyword trick: a text
    containing 'hold' is held and gets one classifier event)."""
    ids = []
    for i in range(n):
        resp = client.post(
            "/messages",
            headers={"X-Mock-User-Id": "mock-user-1"},
            json={
                "client_msg_id": str(uuid.uuid4()),
                "target_type": "user",
                "target_id": str(uuid.uuid4()),
                "kind": "text",
                "text": f"hold me {i}",
            },
        )
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["id"])
    return ids


def _source(client: TestClient, **kw) -> GatewayQueueSource:
    return GatewayQueueSource("http://testserver", "token", headers=MOD, client=client, **kw)


# -- the happy path, through the real request/response shape ---------------


def test_fetch_queue_returns_a_held_message(mock_client) -> None:
    ids = _hold(mock_client)
    items = _source(mock_client).fetch_queue().items
    assert [i.message_id for i in items] == [mock_app._as_uuid(ids[0])]
    assert items[0].latest_event.action.value == "HOLD"
    assert items[0].event_count == 1


def test_release_removes_it_from_the_queue_and_appends_a_moderator_event(mock_client) -> None:
    _hold(mock_client)
    src = _source(mock_client)
    item = src.fetch_queue().items[0]

    out = src.review(
        item.message_id,
        release=True,
        body=ModerationReviewIn(note="Fine.", expected_event_id=item.latest_event.id),
    )

    assert out.event.actor_kind.value == "moderator"
    assert out.event.note == "Fine."
    assert src.fetch_queue().items == []
    trail = src.fetch_events(item.message_id).events
    assert [e.actor_kind.value for e in trail] == ["classifier", "moderator"], (
        "append-only: the classifier's event is still there, the decision is a new row"
    )


# -- the bug Veerendra found: paging ---------------------------------------


def test_fetch_queue_follows_the_cursor_past_the_first_page(mock_client) -> None:
    """The first version made one request and dropped `next_cursor`. With
    more held messages than one page, a moderator would clear the first page
    and see an empty queue while more were still waiting."""
    _hold(mock_client, 30)
    items = _source(mock_client, page_size=10).fetch_queue().items  # three pages
    assert len(items) == 30
    assert len({i.message_id for i in items}) == 30, "no page overlap, nothing duplicated"


def test_the_largest_page_we_ask_for_is_accepted_and_returns_everything(mock_client) -> None:
    """Not a regression test for the paging bug -- the test above is. This
    checks that the page size we use in production (the endpoint's maximum,
    100) is within what the server accepts, and that a queue which fits on
    one page comes back whole. An earlier version of this test claimed to
    reproduce the original bug but asked for 100 per page, so 30 items never
    came near the server's default of 25 and it passed with the bug present;
    a mutation check caught that."""
    _hold(mock_client, 30)
    assert len(_source(mock_client, page_size=100).fetch_queue().items) == 30


def test_a_server_that_repeats_its_cursor_does_not_loop_forever() -> None:
    page = {
        "contract_version": "0.6.0",
        "items": [],
        "next_cursor": "same",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=page))
    src = GatewayQueueSource(
        "http://gw", "t", client=httpx.Client(transport=transport, base_url="http://gw")
    )
    with pytest.raises(SourceError, match="same page cursor twice"):
        src.fetch_queue()


# -- the 409 split: stale id, or wrong state --------------------------------


def test_a_stale_expected_event_id_is_a_conflict_not_an_error(mock_client) -> None:
    _hold(mock_client)
    src = _source(mock_client)
    item = src.fetch_queue().items[0]
    with pytest.raises(ReviewConflict):
        src.review(
            item.message_id,
            release=False,
            body=ModerationReviewIn(expected_event_id=uuid.uuid4()),
        )
    assert len(src.fetch_queue().items) == 1, "refused means refused: still queued"


def test_deciding_a_message_already_decided_is_a_conflict(mock_client) -> None:
    """The real gateway answers 409 for 'not in a state that action
    accepts' as well, and one banner covers both."""
    _hold(mock_client)
    src = _source(mock_client)
    item = src.fetch_queue().items[0]
    src.review(item.message_id, release=False, body=ModerationReviewIn())  # block it
    with pytest.raises(ReviewConflict):
        src.review(item.message_id, release=False, body=ModerationReviewIn())  # block again


# -- every failure that is not a conflict says something --------------------


def test_a_non_moderator_gets_a_message_a_person_can_act_on(mock_client) -> None:
    src = GatewayQueueSource("http://testserver", "t", client=mock_client)  # no role header
    with pytest.raises(SourceError, match="not a moderator"):
        src.fetch_queue()


def test_an_unknown_message_is_a_readable_404(mock_client) -> None:
    with pytest.raises(SourceError, match="404"):
        _source(mock_client).fetch_events(uuid.uuid4())


def _transport_src(handler) -> GatewayQueueSource:
    return GatewayQueueSource(
        "http://gw",
        "t",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gw"),
    )


def test_a_rejected_token_is_a_401_message() -> None:
    src = _transport_src(lambda r: httpx.Response(401, json={"detail": "Not authenticated"}))
    with pytest.raises(SourceError, match="did not accept this console's token"):
        src.fetch_queue()


def test_a_server_error_is_reported_with_its_status() -> None:
    src = _transport_src(lambda r: httpx.Response(503, json={"detail": "db down"}))
    with pytest.raises(SourceError, match=r"503.*db down"):
        src.fetch_queue()


def test_an_unreachable_gateway_says_so() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SourceError, match="Could not reach the gateway"):
        _transport_src(refuse).fetch_queue()


def test_a_timeout_says_nothing_was_changed() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SourceError, match="Nothing was changed"):
        _transport_src(slow).review(uuid.uuid4(), release=True, body=ModerationReviewIn())


def test_an_answer_in_the_wrong_shape_names_the_contract_not_a_traceback() -> None:
    src = _transport_src(lambda r: httpx.Response(200, json={"items": "nope"}))
    with pytest.raises(SourceError, match="different contract version"):
        src.fetch_queue()


# -- choosing a backend from the environment --------------------------------


def test_the_default_backend_is_the_fixture() -> None:
    assert isinstance(build_source_from_env({}), FixtureQueueSource)


def test_a_gateway_backend_needs_a_url_and_a_token() -> None:
    with pytest.raises(ConsoleConfigError, match="CONSOLE_GATEWAY_URL"):
        build_source_from_env({"CONSOLE_SOURCE": "gateway", "CONSOLE_GATEWAY_TOKEN": "t"})
    with pytest.raises(ConsoleConfigError, match="CONSOLE_GATEWAY_TOKEN"):
        build_source_from_env({"CONSOLE_SOURCE": "gateway", "CONSOLE_GATEWAY_URL": "http://g"})


def test_an_unknown_backend_is_refused_at_startup() -> None:
    with pytest.raises(ConsoleConfigError, match="CONSOLE_SOURCE"):
        build_source_from_env({"CONSOLE_SOURCE": "gatway"})


def test_extra_headers_are_parsed_and_validated() -> None:
    env = {
        "CONSOLE_SOURCE": "gateway",
        "CONSOLE_GATEWAY_URL": "http://g",
        "CONSOLE_GATEWAY_TOKEN": "secret-token",
        "CONSOLE_GATEWAY_HEADERS": '{"X-Mock-Role": "moderator"}',
    }
    src = build_source_from_env(env)
    assert isinstance(src, GatewayQueueSource)
    assert src.label == "Gateway — http://g"

    for bad in ("not json", '["a"]', '{"k": 1}'):
        with pytest.raises(ConsoleConfigError, match="CONSOLE_GATEWAY_HEADERS"):
            build_source_from_env({**env, "CONSOLE_GATEWAY_HEADERS": bad})


def test_the_token_never_appears_in_the_label_or_an_error() -> None:
    """The label goes on screen. A token in it, or in a config error, would
    put the one credential this console holds in a screenshot."""
    env = {
        "CONSOLE_SOURCE": "gateway",
        "CONSOLE_GATEWAY_URL": "http://g",
        "CONSOLE_GATEWAY_TOKEN": "secret-token",
    }
    assert "secret-token" not in build_source_from_env(env).label
    with pytest.raises(ConsoleConfigError) as err:
        build_source_from_env({**env, "CONSOLE_GATEWAY_HEADERS": "not json"})
    assert "secret-token" not in str(err.value)
