"""
Tests for the moderator console's state and its fixture backend.

Reflex blocks direct State instantiation outside its runtime unless
`PYTEST_CURRENT_TEST` is set (`reflex.state.is_testing_env`) -- true under
plain pytest, so no harness is needed. Same arrangement as
clients/elder-app/tests/test_state.py.

What these cover is the behaviour the proposal requires, not the layout:
the queue loads, a decision appends to the trail and removes the row, a
stale decision is refused rather than silently applied, and a failed
sender-notice is surfaced instead of swallowed.
"""

from __future__ import annotations

import uuid

import pytest
from admin_console.source import FixtureQueueSource, ReviewConflict

from contracts.chat.moderation import ModerationActorKind, ModerationReviewIn


@pytest.fixture
def source() -> FixtureQueueSource:
    # A fresh instance per test: the fixture backend mutates in memory.
    return FixtureQueueSource()


def test_fixtures_validate_against_the_contract(source: FixtureQueueSource) -> None:
    """The fixture is generated from contracts/chat/moderation.py and
    re-validated on load, so drift fails here rather than in the UI."""
    queue = source.fetch_queue()
    assert len(queue.items) == 3
    assert queue.contract_version


def test_queue_covers_the_three_cases_the_console_must_handle(
    source: FixtureQueueSource,
) -> None:
    items = source.fetch_queue().items
    assert any(i.original_text and i.pivot_text_en for i in items), "text with a pivot"
    assert any(i.original_media_ref is not None for i in items), "a voice note"
    assert any(i.latest_event.degraded for i in items), "a fail-closed hold"


def test_a_degraded_hold_has_no_meaningful_confidence(source: FixtureQueueSource) -> None:
    degraded = next(i for i in source.fetch_queue().items if i.latest_event.degraded)
    assert degraded.latest_event.confidence == 0.0
    assert degraded.pivot_text_en is None, (
        "the pipeline failed before a pivot existed; the console must say so"
    )


def test_release_appends_to_the_trail_and_leaves_the_queue(
    source: FixtureQueueSource,
) -> None:
    item = source.fetch_queue().items[0]
    before = len(source.fetch_events(item.message_id).events)

    out = source.review(
        item.message_id,
        release=True,
        body=ModerationReviewIn(note="Devotional idiom, not a dispute."),
    )

    assert out.message_status == "sent"
    assert out.event.actor_kind is ModerationActorKind.MODERATOR
    assert out.event.confidence is None, "a human reviewer has no confidence score"
    assert out.event.note == "Devotional idiom, not a dispute."

    after = source.fetch_events(item.message_id).events
    assert len(after) == before + 1, "append-only: the trail grows, nothing is replaced"
    assert all(i.message_id != item.message_id for i in source.fetch_queue().items)


def test_block_records_the_notice_only_when_one_really_went_out() -> None:
    """`notice_text` is "what the sender was actually told", so it is only
    recorded when a notice was delivered. Asked for the happy path
    explicitly: the default mirrors the real gateway, which does not yet."""
    delivering = FixtureQueueSource(notices_delivered=True)
    item = delivering.fetch_queue().items[0]
    out = delivering.review(item.message_id, release=False, body=ModerationReviewIn())
    assert out.message_status == "blocked"
    assert out.notice_sent is True
    assert out.event.notice_text, "a blocked sender must be told, and the trail records what"


def test_the_default_fixture_does_not_promise_a_notice_the_gateway_does_not_send(
    source: FixtureQueueSource,
) -> None:
    """An earlier fixture returned notice_sent=True and told the sender they
    could appeal. The real gateway sends no notice yet and appeals are Week 9,
    so a fixture people copy behaviour from must not claim either."""
    item = source.fetch_queue().items[0]
    out = source.review(item.message_id, release=False, body=ModerationReviewIn())
    assert out.notice_sent is False
    assert out.event.notice_text is None
    assert "appeal" not in (out.event.rationale + (out.event.note or "")).lower()


def test_a_moderator_can_correct_the_classifier_label(source: FixtureQueueSource) -> None:
    """A corrected label is the raw material for the exemplar list."""
    from contracts.chat.moderation import ModerationLabel

    item = next(i for i in source.fetch_queue().items if i.latest_event.label.value.startswith("E"))
    out = source.review(
        item.message_id,
        release=True,
        body=ModerationReviewIn(label=ModerationLabel.A_DEVOTIONAL),
    )
    assert out.event.label is ModerationLabel.A_DEVOTIONAL


def test_label_stands_when_the_moderator_does_not_correct_it(
    source: FixtureQueueSource,
) -> None:
    item = source.fetch_queue().items[0]
    original_label = item.latest_event.label
    out = source.review(item.message_id, release=True, body=ModerationReviewIn())
    assert out.event.label is original_label


def test_a_stale_decision_is_refused_not_applied(source: FixtureQueueSource) -> None:
    """Two moderators with the same item open: the second click must not
    silently overwrite a decision it never saw."""
    item = source.fetch_queue().items[0]
    stale_event_id = uuid.uuid4()  # not the message's latest event

    with pytest.raises(ReviewConflict):
        source.review(
            item.message_id,
            release=False,
            body=ModerationReviewIn(expected_event_id=stale_event_id),
        )

    # Refused means refused: still queued, trail untouched.
    assert any(i.message_id == item.message_id for i in source.fetch_queue().items)
    assert len(source.fetch_events(item.message_id).events) == 1


def test_a_current_expected_event_id_is_accepted(source: FixtureQueueSource) -> None:
    item = source.fetch_queue().items[0]
    out = source.review(
        item.message_id,
        release=True,
        body=ModerationReviewIn(expected_event_id=item.latest_event.id),
    )
    assert out.event.id != item.latest_event.id


def test_reviewing_an_unknown_message_raises(source: FixtureQueueSource) -> None:
    with pytest.raises(KeyError):
        source.review(uuid.uuid4(), release=True, body=ModerationReviewIn())


# -- state layer ----------------------------------------------------------


def test_state_loads_flattens_and_selects() -> None:
    from admin_console import admin_console as console

    console.set_source(FixtureQueueSource())
    state = console.State()
    state.load_queue()

    assert len(state.rows) == 3
    assert all(r.author for r in state.rows)
    # The side-by-side requirement, at the data level: a row either has a
    # pivot or is explicitly marked as not having one.
    assert any(r.has_pivot for r in state.rows)
    assert any(not r.has_pivot for r in state.rows)

    state.select(state.rows[0].message_id)
    assert state.trail, "selecting a message loads its audit trail"


def test_state_surfaces_a_conflict_instead_of_applying_it() -> None:
    from admin_console import admin_console as console

    src = FixtureQueueSource()
    console.set_source(src)
    state = console.State()
    state.load_queue()
    state.select(state.rows[0].message_id)

    # Someone else decides it first, behind this moderator's back.
    src.review(uuid.UUID(state.selected_id), release=True, body=ModerationReviewIn())

    state.block()
    assert state.banner_is_error
    assert "Not applied" in state.banner


# -- failures that are not conflicts must reach the moderator ---------------


class _FailingSource:
    """Every call fails the way a down or refusing gateway would."""

    label = "Gateway — http://down"

    def __init__(self, message: str = "boom") -> None:
        self._message = message

    def fetch_queue(self):
        from admin_console.source import SourceError

        raise SourceError(self._message)

    def fetch_events(self, message_id):
        from admin_console.source import SourceError

        raise SourceError(self._message)

    def review(self, message_id, *, release, body):
        from admin_console.source import SourceError

        raise SourceError(self._message)


def test_a_failed_refresh_keeps_the_last_queue_and_says_so() -> None:
    """Blanking the queue would read as 'nothing is waiting' -- the one
    wrong thing to imply when the truth is 'I could not ask'."""
    from admin_console import admin_console as console

    console.set_source(FixtureQueueSource())
    state = console.State()
    state.load_queue()
    before = list(state.rows)
    assert before

    console.set_source(_FailingSource("The gateway did not answer within 10 s."))
    state.load_queue()

    assert state.rows == before, "the last list stays on screen"
    assert state.banner_is_error
    assert "Could not refresh the queue" in state.banner
    assert "last list loaded" in state.banner
    assert state.source_label == "Gateway — http://down"


def test_a_failed_decision_is_reported_and_keeps_the_selection() -> None:
    from admin_console import admin_console as console

    console.set_source(FixtureQueueSource())
    state = console.State()
    state.load_queue()
    state.select(state.rows[0].message_id)
    chosen = state.selected_id

    console.set_source(_FailingSource("This account is not a moderator (403)."))
    state.release()

    assert state.banner_is_error
    assert state.banner.startswith("Not applied")
    assert "not a moderator" in state.banner
    assert state.selected_id == chosen, "kept, so the moderator can simply try again"


def test_a_failed_audit_trail_load_is_reported() -> None:
    from admin_console import admin_console as console

    console.set_source(FixtureQueueSource())
    state = console.State()
    state.load_queue()
    console.set_source(_FailingSource("The gateway returned an error (503)."))
    state.select(state.rows[0].message_id)

    assert state.banner_is_error
    assert "audit trail" in state.banner
    assert state.trail == []


def test_a_successful_decision_followed_by_a_failed_refresh_shows_both() -> None:
    """The decision went through; the refresh did not. Letting the second
    message overwrite the first would make a successful release look like it
    never happened."""
    from admin_console import admin_console as console

    class _FlakyRefresh(FixtureQueueSource):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next_queue = False

        def fetch_queue(self):
            if self.fail_next_queue:
                from admin_console.source import SourceError

                raise SourceError("The gateway did not answer.")
            return super().fetch_queue()

    src = _FlakyRefresh()
    console.set_source(src)
    state = console.State()
    state.load_queue()
    state.select(state.rows[0].message_id)
    src.fail_next_queue = True
    state.release()

    assert "Released" in state.banner
    assert "Could not refresh the queue" in state.banner
    assert state.banner_is_error
