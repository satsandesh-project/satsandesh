"""OPEN_QUESTIONS #18: the sender's notice reaches the sender, and only the sender.

M4's contract 0.6.0 (`MessageOut.moderation_notice` / `moderation_notice_language`, author-only) is
merged; until now the gateway never filled it in, so a sender whose note was nudged, held or blocked
saw a status and no reason. These tests pin the three things that matter:

  * the AUTHOR gets the notice, in the language it was written in, on every read path (HTTP sync,
    WebSocket sync.batch), so an elder who was offline when a moderator acted still sees it;
  * nobody else does: a NUDGE-delivered DM is read by its recipient, who must see no notice. This is
    this project's recurring bug class (#25: a field "hidden" by a fan-out rule but not by the read
    path), so the recipient is read through BOTH paths;
  * the sender is told the notice and nothing about the classifier: no label, confidence or
    rationale (a precise reason why something was blocked is also a guide to evading the classifier).

The notice shown is the one on the LATEST event. If a moderator ruled after the classifier, the
sender is not shown the classifier's older text as if it were still true (a `held` notice on a note
that was since blocked): they see the status and no stale reason.

Written before the gateway fills the field in.
"""

import uuid

import pytest

from app.db.models import User as DbUser
from app.db.moderation import notices_for_author, record_moderation_event
from app.db.repository import create_message, set_message_status

NOTICE = "మీ సందేశం సమీక్షలో ఉంది."
RATIONALE = "Matched the personal-matters rule (C3); confidence 0.91."
SECRET_WORDS = ("C3", "0.91", "rationale", "C_PERSONAL", "classifier")


def _user(db_session, name, role="elder"):
    user = DbUser(name=name, preferred_language="te", role=role)
    db_session.add(user)
    db_session.flush()
    return user


def _classifier_event(db_session, message_id, *, action, notice=NOTICE, language="te"):
    return record_moderation_event(
        db_session,
        message_id=message_id,
        actor_kind="classifier",
        label="C_PERSONAL",
        action=action,
        confidence=0.91,
        rationale=RATIONALE,
        policy_version="policy@2026-09-22",
        model_version="stub",
        notice_text=notice,
        notice_language=language if notice else None,
    )


def _dm(db_session, author, target, *, status, action="HOLD", notice=NOTICE, language="te"):
    message = create_message(
        db_session,
        author_id=author.id,
        target_type="user",
        target_user_id=target.id,
        kind="text",
        text="a private matter",
        client_msg_id=uuid.uuid4(),
    )
    db_session.commit()
    if status != "pending":
        assert set_message_status(db_session, message.id, new_status=status, expected="pending")
    _classifier_event(db_session, message.id, action=action, notice=notice, language=language)
    db_session.commit()
    return message.id


# --- the rule, as a function --------------------------------------------------------------------


def test_the_author_gets_the_notice_and_the_language_it_was_written_in(db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status="held")
    from app.db.models import Message

    message = db_session.get(Message, message_id)

    assert notices_for_author(db_session, [message], alice.id) == {str(message_id): (NOTICE, "te")}


def test_nobody_but_the_author_gets_it_by_construction(db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status="sent", action="NUDGE")
    from app.db.models import Message

    message = db_session.get(Message, message_id)

    assert notices_for_author(db_session, [message], bob.id) == {}, "the recipient"
    assert notices_for_author(db_session, [message], uuid.uuid4()) == {}, "a stranger"


def test_an_event_without_a_notice_gives_the_sender_nothing_to_show(db_session):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status="held", notice=None)
    from app.db.models import Message

    message = db_session.get(Message, message_id)

    assert notices_for_author(db_session, [message], alice.id) == {}


def test_a_later_moderator_ruling_replaces_the_classifiers_notice_it_is_not_shown_stale(
    db_session,
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    moderator = _user(db_session, "Mod", role="moderator")
    message_id = _dm(db_session, alice, bob, status="held")
    record_moderation_event(
        db_session,
        message_id=message_id,
        actor_kind="moderator",
        actor_id=moderator.id,
        label="E_HARMFUL",
        action="BLOCK",
        rationale="Reviewed.",
        policy_version="policy@2026-09-22",
    )
    db_session.commit()
    from app.db.models import Message

    message = db_session.get(Message, message_id)

    assert notices_for_author(db_session, [message], alice.id) == {}


# --- the read paths --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "action"), [("held", "HOLD"), ("blocked", "BLOCK"), ("sent", "NUDGE")]
)
def test_http_sync_shows_the_author_the_notice_for_a_nudge_hold_or_block(
    client, db_session, login_as, status, action
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _dm(db_session, alice, bob, status=status, action=action)

    login_as(alice)
    response = client.get("/messages", params={"target_type": "user", "target_id": str(bob.id)})

    (message,) = response.json()["messages"]
    assert message["moderation_notice"] == NOTICE
    assert message["moderation_notice_language"] == "te"


def test_http_sync_never_shows_the_recipient_the_notice_of_a_nudged_dm_they_can_read(
    client, db_session, login_as
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status="sent", action="NUDGE")

    login_as(bob)
    response = client.get("/messages", params={"target_type": "user", "target_id": str(alice.id)})

    (message,) = response.json()["messages"]
    assert message["id"] == str(message_id), "the recipient does read this message"
    assert message["moderation_notice"] is None
    assert message["moderation_notice_language"] is None
    assert NOTICE not in response.text


def test_the_sender_is_told_the_notice_and_nothing_about_the_classifier(
    client, db_session, login_as
):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _dm(db_session, alice, bob, status="blocked", action="BLOCK")

    login_as(alice)
    response = client.get("/messages", params={"target_type": "user", "target_id": str(bob.id)})

    for secret in SECRET_WORDS:
        assert secret not in response.text, f"the sender must not be handed {secret!r}"


def test_websocket_sync_shows_the_author_and_not_the_recipient(client, db_session, ws_login_as):
    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    _dm(db_session, alice, bob, status="sent", action="NUDGE")

    def sync(user, other):
        token = ws_login_as(user)
        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_json(
                {
                    "type": "sync.request",
                    "data": {"target_type": "user", "target_id": str(other.id)},
                }
            )
            frame = ws.receive_json()
        assert frame["type"] == "sync.batch"
        return frame["data"]["messages"]

    (as_author,) = sync(alice, bob)
    (as_recipient,) = sync(bob, alice)

    assert (
        as_author["moderation_notice"] == NOTICE and as_author["moderation_notice_language"] == "te"
    )
    assert as_recipient["moderation_notice"] is None
    assert as_recipient["moderation_notice_language"] is None


# --- the live half: the message.status frame -----------------------------------------------------


def test_the_status_frame_to_the_sender_carries_the_notice(db_session):
    from app.pipeline import build_status_frame

    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status="held")

    recipient_id, frame = build_status_frame(db_session, message_id, "held")

    assert recipient_id == alice.id, "addressed to the author's devices and nobody else's"
    assert frame["type"] == "message.status"
    assert frame["data"]["status"] == "held"
    assert frame["data"]["notice_text"] == NOTICE and frame["data"]["notice_language"] == "te"
    for secret in SECRET_WORDS:
        assert secret not in str(frame), f"the frame must not carry {secret!r}"


def test_a_status_frame_owing_no_notice_carries_none(db_session):
    # A pipeline failure holds the message with a SYSTEM event that has no notice: the frame says
    # `held` and nothing else. The wording for that case is M4's call, not invented here.
    from app.pipeline import build_status_frame

    alice, bob = _user(db_session, "Alice"), _user(db_session, "Bob")
    message_id = _dm(db_session, alice, bob, status="held", notice=None)

    _recipient, frame = build_status_frame(db_session, message_id, "held")

    assert frame["data"]["notice_text"] is None and frame["data"]["notice_language"] is None


def test_there_is_no_frame_for_a_message_that_no_longer_exists(db_session):
    from app.pipeline import build_status_frame

    assert build_status_frame(db_session, uuid.uuid4(), "held") is None
