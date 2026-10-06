import uuid
from datetime import UTC, datetime

import pytest
from contracts.ai.moderation import ModerationAction as AiModerationAction
from contracts.ai.moderation import ModerationLabel as AiModerationLabel
from contracts.chat.common import AudioFormat, MediaRef
from contracts.chat.moderation import (
    ModerationAction,
    ModerationActorKind,
    ModerationEvent,
    ModerationEventsOut,
    ModerationLabel,
    ModerationQueueItem,
    ModerationQueueOut,
    ModerationReviewIn,
    ModerationReviewOut,
)
from pydantic import ValidationError


def _event(**overrides) -> ModerationEvent:
    base = {
        "id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "actor_kind": ModerationActorKind.CLASSIFIER,
        "label": ModerationLabel.D_DISPUTATIONAL,
        "action": ModerationAction.HOLD,
        "confidence": 0.81,
        "rationale": "Criticism of a named person.",
        "policy_version": "policy@2026-09-22",
        "model_version": "llama.cpp:qwen2.5-3b-instruct-q4_k_m.gguf",
        "created_at": datetime.now(UTC),
    }
    base.update(overrides)
    return ModerationEvent(**base)


def test_label_and_action_values_match_the_ai_package() -> None:
    """These enums are deliberately duplicated rather than imported
    (contracts/chat/DECISIONS.md #5). Duplication is only safe while the
    values agree, so the drift guard is this test -- not an import."""
    assert {m.value for m in ModerationLabel} == {m.value for m in AiModerationLabel}
    assert {m.value for m in ModerationAction} == {m.value for m in AiModerationAction}


def test_event_is_not_a_versioned_payload() -> None:
    # Nested value object, like MediaRef -- DECISIONS.md #9.
    assert not hasattr(_event(), "contract_version")


def test_classifier_event_has_no_actor_id_but_a_human_one_does() -> None:
    assert _event().actor_id is None
    reviewer = uuid.uuid4()
    human = _event(
        actor_kind=ModerationActorKind.MODERATOR,
        actor_id=reviewer,
        confidence=None,
        model_version=None,
        note="Released: devotional idiom, not a dispute.",
        action=ModerationAction.ALLOW,
    )
    assert human.actor_id == reviewer
    assert human.confidence is None, "a human reviewer has no confidence score"
    assert human.model_version is None


def test_confidence_is_bounded() -> None:
    for bad in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            _event(confidence=bad)
    assert _event(confidence=0.0).confidence == 0.0
    assert _event(confidence=1.0).confidence == 1.0


def test_degraded_defaults_false_and_marks_a_fail_closed_hold() -> None:
    assert _event().degraded is False
    fallback = _event(confidence=0.0, rationale="No usable verdict; held.", degraded=True)
    assert fallback.degraded is True
    assert fallback.action is ModerationAction.HOLD


def test_notice_text_is_recorded_per_event() -> None:
    """What the sender saw at the time, not what the current policy would
    say -- the wording can change between a hold and its appeal."""
    e = _event(notice_text="మీ సందేశం సమీక్ష కోసం వేచి ఉంది.")
    assert e.notice_text


def test_queue_item_carries_original_and_pivot_side_by_side() -> None:
    item = ModerationQueueItem(
        message_id=uuid.uuid4(),
        author_id=uuid.uuid4(),
        author_display_name="Kamala",
        target_type="circle",
        target_id=uuid.uuid4(),
        original_text="నేను నా అహంకారాన్ని చంపాలనుకుంటున్నాను.",
        original_language="te",
        pivot_text_en="I want to kill my ego.",
        latest_event=_event(),
        event_count=1,
        created_at=datetime.now(UTC),
    )
    assert item.original_text and item.pivot_text_en, (
        "the proposal requires original and translation side by side"
    )


def test_queue_item_allows_a_voice_note_with_no_text() -> None:
    item = ModerationQueueItem(
        message_id=uuid.uuid4(),
        author_id=uuid.uuid4(),
        author_display_name="Ramesh",
        target_type="user",
        target_id=uuid.uuid4(),
        original_media_ref=MediaRef(uri="media:abc123", format=AudioFormat.WEBM_OPUS),
        pivot_text_en="Please pray for my husband.",
        latest_event=_event(),
        event_count=2,
        created_at=datetime.now(UTC),
    )
    assert item.original_text is None
    assert item.original_media_ref is not None


def test_queue_item_allows_a_missing_pivot() -> None:
    """The pipeline can fail before producing one; the moderator then
    reviews the original alone and should be shown that."""
    item = ModerationQueueItem(
        message_id=uuid.uuid4(),
        author_id=uuid.uuid4(),
        author_display_name="Lakshmi",
        target_type="circle",
        target_id=uuid.uuid4(),
        original_text="...",
        latest_event=_event(degraded=True),
        event_count=1,
        created_at=datetime.now(UTC),
    )
    assert item.pivot_text_en is None


def test_event_count_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        ModerationQueueItem(
            message_id=uuid.uuid4(),
            author_id=uuid.uuid4(),
            author_display_name="X",
            target_type="circle",
            target_id=uuid.uuid4(),
            latest_event=_event(),
            event_count=0,
            created_at=datetime.now(UTC),
        )


def test_queue_and_events_are_versioned_top_level_payloads() -> None:
    q = ModerationQueueOut(items=[])
    assert q.contract_version
    assert q.next_cursor is None
    ev = ModerationEventsOut(message_id=uuid.uuid4(), events=[_event()])
    assert ev.contract_version


def test_review_in_carries_no_action_field() -> None:
    """The action lives in the route, not the body: a console bug must not
    be able to turn a release into a block."""
    assert "action" not in ModerationReviewIn.model_fields


def test_review_in_is_valid_empty_and_supports_a_label_correction() -> None:
    assert ModerationReviewIn().note is None
    corrected = ModerationReviewIn(
        note="Not a dispute -- devotional idiom.",
        label=ModerationLabel.A_DEVOTIONAL,
        expected_event_id=uuid.uuid4(),
    )
    assert corrected.label is ModerationLabel.A_DEVOTIONAL
    assert corrected.expected_event_id is not None


def test_review_in_note_is_length_bounded() -> None:
    with pytest.raises(ValidationError):
        ModerationReviewIn(note="x" * 2001)


def test_review_out_reports_whether_the_sender_was_told() -> None:
    out = ModerationReviewOut(
        event=_event(actor_kind=ModerationActorKind.MODERATOR, actor_id=uuid.uuid4()),
        message_status="sent",
        notice_sent=False,
    )
    assert out.notice_sent is False, (
        "'never silently' is a promise about the sender's experience, so the console "
        "has to be able to see when the notice did not go out"
    )


# -- Week 7: answers to issue #65's three questions ------------------------


def test_nudge_delivery_semantics_are_documented_on_the_enum() -> None:
    """Issue #65 implemented NUDGE as delivering, because this enum said
    nothing about delivery. The policy document says a NUDGE is "not
    delivered to circles" -- so the semantics live here now, where someone
    building against the contract will actually read them."""
    doc = ModerationAction.__doc__ or ""
    assert "not delivered to a circle" in doc.lower()
    assert "knob" in doc.lower(), "the 1:1 case is the organisation's decision, not ours"


def test_queue_item_carries_a_transcript_separate_from_original_text() -> None:
    """A transcript is what the machine heard; original_text is what the
    sender typed. Collapsing them would hide ASR error from the moderator."""
    item = ModerationQueueItem(
        message_id=uuid.uuid4(),
        author_id=uuid.uuid4(),
        author_display_name="Kamala",
        target_type="circle",
        target_id=uuid.uuid4(),
        original_media_ref=MediaRef(uri="media:abc", format=AudioFormat.WEBM_OPUS),
        original_language="te",
        transcript="నేను నా అహంకారాన్ని చంపాలనుకుంటున్నాను.",
        transcript_language="te",
        pivot_text_en="I want to kill my ego.",
        latest_event=_event(),
        event_count=1,
        created_at=datetime.now(UTC),
    )
    assert item.original_text is None, "a voice note has no typed text"
    assert item.transcript and item.transcript_language == "te"
    assert item.pivot_text_en, "and the English pivot stays its own field"


def test_transcript_language_is_a_language_tag() -> None:
    from pydantic import ValidationError as VE

    with pytest.raises(VE):
        ModerationQueueItem(
            message_id=uuid.uuid4(),
            author_id=uuid.uuid4(),
            author_display_name="X",
            target_type="user",
            target_id=uuid.uuid4(),
            transcript="hello",
            transcript_language="Telugu",  # not a 2-3 letter tag
            latest_event=_event(),
            event_count=1,
            created_at=datetime.now(UTC),
        )


def test_message_out_carries_an_author_only_moderation_notice() -> None:
    """Without this the sender is never told, and
    ModerationReviewOut.notice_sent could only ever be false."""
    from contracts.chat.common import MessageKind, MessageStatus, TargetType
    from contracts.chat.messages import MessageOut

    held = MessageOut(
        id="01J0",
        author_id="u1",
        target_type=TargetType.CIRCLE,
        target_id="c1",
        kind=MessageKind.TEXT,
        text="...",
        moderation_notice="మీ సందేశం సమీక్ష కోసం వేచి ఉంది.",
        moderation_notice_language="te",
        created_at=datetime.now(UTC),
        status=MessageStatus.HELD,
    )
    assert held.moderation_notice and held.moderation_notice_language == "te"

    # Absent by default: an allowed message owes no notice, and every
    # non-author reader must see null (the gateway enforces that).
    allowed = MessageOut(
        id="01J1",
        author_id="u1",
        target_type=TargetType.CIRCLE,
        target_id="c1",
        kind=MessageKind.TEXT,
        text="Om Sai Ram.",
        created_at=datetime.now(UTC),
        status=MessageStatus.SENT,
    )
    assert allowed.moderation_notice is None


def test_message_status_frame_can_carry_the_live_notice() -> None:
    from contracts.chat.common import MessageStatus
    from contracts.chat.messages import MessageStatusOut

    frame = MessageStatusOut(
        id="01J0",
        status=MessageStatus.BLOCKED,
        notice_text="This message was not sent. You can reply to appeal.",
        notice_language="en",
    )
    assert frame.notice_text and frame.notice_language == "en"
    assert MessageStatusOut(id="01J1", status=MessageStatus.DELIVERED).notice_text is None
