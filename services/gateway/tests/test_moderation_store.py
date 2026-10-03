"""Week 7 Phase 3: the append-only moderation trail.

`contracts/chat/moderation.py` (M4's, issue #65) describes the trail but
leaves the table to the gateway. Its rules are enforced here at the storage
layer, not left to callers: a classifier or system event has no actor, a
human decision carries no model opinion, and -- the point of an audit trail
-- a row, once written, cannot be changed or removed.

Written before the table, the model or the repository exist.
"""

import uuid

import pytest
from contracts.ai.common import DegradedMode, DegradedReason
from contracts.ai.moderation import ModerationAction as AiAction
from contracts.ai.moderation import ModerationDecision
from contracts.ai.moderation import ModerationLabel as AiLabel
from contracts.chat import moderation as chat_moderation
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.db.models import ModerationEvent
from app.db.models import User as DbUser
from app.db.moderation import (
    count_moderation_events,
    latest_moderation_event,
    list_moderation_events,
    moderation_event_to_out,
    record_classifier_decision,
    record_moderation_event,
)
from app.db.repository import create_message


def _user(db_session, name="Alice", role="elder"):
    user = DbUser(name=name, preferred_language="te", role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _message(db_session):
    author = _user(db_session, "Author")
    target = _user(db_session, "Target")
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
    return message.id


def _classifier_event(db_session, message_id, **overrides):
    kwargs = {
        "message_id": message_id,
        "actor_kind": "classifier",
        "label": "D_DISPUTATIONAL",
        "action": "HOLD",
        "rationale": "Criticism of a named person.",
        "policy_version": "policy@2026-09-22",
        "confidence": 0.81,
        "model_version": "llama.cpp:qwen2.5-3b-instruct-q4_k_m.gguf",
    }
    kwargs.update(overrides)
    event = record_moderation_event(db_session, **kwargs)
    db_session.commit()
    return event


# --- writing and reading -----------------------------------------------------------


def test_a_classifier_event_round_trips_every_field(db_session):
    message_id = _message(db_session)
    event = _classifier_event(db_session, message_id, notice_text="Please be gentle.")
    event_id = event.id

    db_session.expire_all()
    stored = db_session.get(ModerationEvent, event_id)
    assert stored.message_id == message_id
    assert (stored.actor_kind, stored.actor_id) == ("classifier", None)
    assert (stored.label, stored.action) == ("D_DISPUTATIONAL", "HOLD")
    assert stored.confidence == pytest.approx(0.81)
    assert stored.notice_text == "Please be gentle."
    assert stored.policy_version == "policy@2026-09-22"
    assert stored.degraded is False and stored.degraded_reason is None
    assert stored.created_at is not None


def test_a_moderators_decision_carries_the_moderator_and_no_model_opinion(db_session):
    message_id = _message(db_session)
    moderator = _user(db_session, "Mod", role="moderator")
    event = record_moderation_event(
        db_session,
        message_id=message_id,
        actor_kind="moderator",
        actor_id=moderator.id,
        label="A_DEVOTIONAL",
        action="ALLOW",
        rationale="Devotional idiom, not a dispute.",
        note="Released.",
        policy_version="policy@2026-09-22",
    )
    db_session.commit()
    assert event.actor_id == moderator.id
    assert event.confidence is None and event.model_version is None


def test_events_come_back_oldest_first_with_a_count_and_a_latest(db_session):
    message_id = _message(db_session)
    first = _classifier_event(db_session, message_id, action="HOLD")
    second = _classifier_event(db_session, message_id, action="BLOCK", label="E_HARMFUL")
    first_id, second_id = first.id, second.id

    assert [e.id for e in list_moderation_events(db_session, message_id)] == [first_id, second_id]
    assert count_moderation_events(db_session, message_id) == 2
    assert latest_moderation_event(db_session, message_id).id == second_id


def test_a_message_with_no_events_has_none(db_session):
    message_id = _message(db_session)
    assert list_moderation_events(db_session, message_id) == []
    assert count_moderation_events(db_session, message_id) == 0
    assert latest_moderation_event(db_session, message_id) is None


# --- translating the classifier's answer -----------------------------------------------


def _decision(**overrides):
    base = {
        "label": AiLabel.D_DISPUTATIONAL,
        "confidence": 0.62,
        "action": AiAction.HOLD,
        "rationale": "Borderline.",
        "nudge_text": "Please reconsider.",
        "policy_version": "policy@2026-09-22",
        "model_version": "stub",
    }
    base.update(overrides)
    return ModerationDecision(**base)


def test_a_classifier_decision_is_stored_as_the_classifiers_event(db_session):
    message_id = _message(db_session)
    event = record_classifier_decision(db_session, message_id=message_id, decision=_decision())
    db_session.commit()
    assert (event.actor_kind, event.actor_id) == ("classifier", None)
    assert (event.label, event.action) == ("D_DISPUTATIONAL", "HOLD")
    assert event.confidence == pytest.approx(0.62)
    assert (event.policy_version, event.model_version) == ("policy@2026-09-22", "stub")
    assert event.degraded is False


def test_the_english_nudge_is_not_recorded_as_what_the_sender_was_told(db_session):
    """`notice_text` is "what the sender was actually told, in their own
    language". The classifier's nudge is an English master (and the mock's is
    Telugu), so it is only recorded when the caller says that is what was sent."""
    message_id = _message(db_session)
    bare = record_classifier_decision(db_session, message_id=message_id, decision=_decision())
    told = record_classifier_decision(
        db_session,
        message_id=message_id,
        decision=_decision(),
        notice_text="దయచేసి మృదువుగా చెప్పండి.",
    )
    db_session.commit()
    assert bare.notice_text is None
    assert told.notice_text == "దయచేసి మృదువుగా చెప్పండి."


def test_a_degraded_fallback_hold_keeps_why_it_was_degraded(db_session):
    """The chat contract's `degraded` is a bool; the AI service's is
    {active, reason, detail}. The reason is kept (the console should tell
    "model timed out" from "unparseable reply") even though the wire shape
    only needs the bool."""
    message_id = _message(db_session)
    decision = _decision(
        confidence=0.0,
        degraded=DegradedMode(
            active=True, reason=DegradedReason.MODEL_FALLBACK, detail="timeout after 20s"
        ),
    )
    event = record_classifier_decision(db_session, message_id=message_id, decision=decision)
    db_session.commit()
    assert event.degraded is True
    assert event.degraded_reason == "model_fallback"


# --- the actor rules the contract leaves to the gateway ------------------------------------


def test_a_moderator_event_without_a_moderator_is_refused(db_session):
    message_id = _message(db_session)
    with pytest.raises(ValueError, match="actor_id"):
        record_moderation_event(
            db_session,
            message_id=message_id,
            actor_kind="moderator",
            label="A_DEVOTIONAL",
            action="ALLOW",
            rationale="x",
            policy_version="p",
        )


@pytest.mark.parametrize("kind", ["classifier", "system"])
def test_an_automated_event_with_an_actor_is_refused(db_session, kind):
    message_id = _message(db_session)
    someone = _user(db_session, "Someone")
    with pytest.raises(ValueError, match="actor_id"):
        record_moderation_event(
            db_session,
            message_id=message_id,
            actor_kind=kind,
            actor_id=someone.id,
            label="A_DEVOTIONAL",
            action="ALLOW",
            rationale="x",
            policy_version="p",
        )


def test_a_human_event_cannot_carry_a_confidence(db_session):
    """Reusing 0.0 or 1.0 for a person would make "the human was certain"
    indistinguishable from "the model said so" (contract docstring)."""
    message_id = _message(db_session)
    moderator = _user(db_session, "Mod", role="moderator")
    with pytest.raises(ValueError, match="confidence"):
        record_moderation_event(
            db_session,
            message_id=message_id,
            actor_kind="moderator",
            actor_id=moderator.id,
            label="A_DEVOTIONAL",
            action="ALLOW",
            rationale="x",
            policy_version="p",
            confidence=1.0,
        )


# --- the database refuses what the Python layer would also refuse -------------------------
# (a writer that bypasses record_moderation_event -- raw SQL, another service --
# must not be able to create an inconsistent trail either)


def _raw(message_id, **overrides):
    base = {
        "message_id": message_id,
        "actor_kind": "classifier",
        "label": "A_DEVOTIONAL",
        "action": "ALLOW",
        "rationale": "x",
        "policy_version": "p",
    }
    base.update(overrides)
    return ModerationEvent(**base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"actor_kind": "robot"},
        {"label": "F_UNKNOWN"},
        {"action": "SHRUG"},
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"actor_kind": "moderator"},  # no actor_id
        {"actor_kind": "classifier", "actor_id": uuid.uuid4()},  # actor on an automated event
        {"actor_kind": "system", "confidence": 0.5},  # a non-classifier with a model opinion
        {"actor_kind": "system", "model_version": "m"},
        {"degraded": True, "actor_kind": "system"},  # only a classifier can fail closed
        {"degraded_reason": "model_fallback"},  # a reason without degraded
        {"degraded": True, "degraded_reason": "none"},
        {"note": "x" * 2001},
    ],
    ids=lambda o: ",".join(f"{k}={str(v)[:12]}" for k, v in o.items()),
)
def test_the_database_refuses_an_inconsistent_event(db_session, overrides):
    message_id = _message(db_session)
    if overrides.get("actor_kind") == "classifier" and "actor_id" in overrides:
        overrides = {**overrides, "actor_id": _user(db_session, "Real").id}
    db_session.add(_raw(message_id, **overrides))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_an_event_for_a_message_that_does_not_exist_is_refused(db_session):
    db_session.add(_raw(uuid.uuid4()))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


@pytest.mark.parametrize("label", [m.value for m in chat_moderation.ModerationLabel])
@pytest.mark.parametrize("action", [m.value for m in chat_moderation.ModerationAction])
def test_every_label_and_action_the_contract_allows_can_be_stored(db_session, label, action):
    """Drift guard: the table's allowed values are written out in SQL, so the
    contract growing a value the table doesn't know would fail here."""
    message_id = _message(db_session)
    db_session.add(_raw(message_id, label=label, action=action))
    db_session.flush()
    db_session.rollback()


# --- append-only ---------------------------------------------------------------------


def _expect_blocked(db_session, sql, **params):
    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text(sql), params)
    db_session.rollback()


def test_a_stored_event_cannot_be_updated(db_session):
    message_id = _message(db_session)
    event = _classifier_event(db_session, message_id)
    _expect_blocked(
        db_session, "UPDATE moderation_events SET action = 'ALLOW' WHERE id = :id", id=event.id
    )


def test_a_stored_event_cannot_be_deleted(db_session):
    message_id = _message(db_session)
    event = _classifier_event(db_session, message_id)
    _expect_blocked(db_session, "DELETE FROM moderation_events WHERE id = :id", id=event.id)


def test_the_trail_cannot_be_truncated(db_session):
    message_id = _message(db_session)
    _classifier_event(db_session, message_id)
    _expect_blocked(db_session, "TRUNCATE moderation_events")


def test_the_trail_survives_the_refused_attempts(db_session):
    message_id = _message(db_session)
    event = _classifier_event(db_session, message_id)
    event_id = event.id
    for sql in (
        "UPDATE moderation_events SET rationale = 'rewritten'",
        "DELETE FROM moderation_events",
    ):
        with pytest.raises(DBAPIError):
            db_session.execute(text(sql))
        db_session.rollback()
    db_session.expire_all()  # read the database, not the identity map
    assert db_session.get(ModerationEvent, event_id).rationale == "Criticism of a named person."


def test_the_one_deliberate_purge_path_is_explicit_and_transaction_local(db_session):
    """Erasing a person's data on request (contracts/chat/moderation.py's
    SYSTEM actor kind names it) needs *a* way to remove rows. It is a
    deliberate, per-transaction opt-in -- not a default, not a role -- and the
    test fixtures' own cleanup uses it, so it is exercised on every run."""
    message_id = _message(db_session)
    event = _classifier_event(db_session, message_id)
    db_session.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
    db_session.execute(text("DELETE FROM moderation_events WHERE id = :id"), {"id": event.id})
    db_session.commit()
    assert list_moderation_events(db_session, message_id) == []
    # ... and the opt-in did not leak past that transaction.
    event2 = _classifier_event(db_session, message_id)
    _expect_blocked(db_session, "DELETE FROM moderation_events WHERE id = :id", id=event2.id)


# --- fits M4's wire shape ------------------------------------------------------------


def test_a_stored_event_maps_onto_the_chat_contracts_moderation_event(db_session):
    message_id = _message(db_session)
    event = record_classifier_decision(
        db_session,
        message_id=message_id,
        decision=_decision(
            confidence=0.0,
            degraded=DegradedMode(active=True, reason=DegradedReason.MODEL_FALLBACK),
        ),
    )
    db_session.commit()

    out = moderation_event_to_out(event)

    assert isinstance(out, chat_moderation.ModerationEvent)
    assert out.id == event.id and out.message_id == message_id
    assert out.actor_kind is chat_moderation.ModerationActorKind.CLASSIFIER
    assert out.actor_id is None
    assert out.label is chat_moderation.ModerationLabel.D_DISPUTATIONAL
    assert out.action is chat_moderation.ModerationAction.HOLD
    assert out.degraded is True
    assert out.created_at == event.created_at


# --- the migration really installed the guards ----------------------------------------


def test_the_append_only_triggers_exist(db_session):
    names = {
        row[0]
        for row in db_session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid = 'moderation_events'::regclass AND NOT tgisinternal"
            )
        )
    }
    assert names == {"trg_moderation_events_no_row_change", "trg_moderation_events_no_truncate"}
