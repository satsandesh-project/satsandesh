"""
Two-pass classification (services/ai/moderation/classify.py).

The property under test throughout: **two-pass must not change any
decision.** It only changes how many model calls are spent producing the
moderator-facing rationale, and on which messages.
"""

from __future__ import annotations

import pytest
from contracts.ai.common import DegradedReason
from contracts.ai.moderation import ModerationAction, ModerationLabel
from services.ai.moderation.classify import classify
from services.ai.moderation.engine import ClassifierError, StubClassifier

HOLD_T = 0.70
BLOCK_T = 0.85

ALLOW_TEXT = "Om Sai Ram. May everyone have a peaceful morning."
NUDGE_TEXT = "I am selling my old scooter, good condition, 25,000 rupees."
HOLD_TEXT = "The way the bhajans are conducted now is completely wrong."
BLOCK_TEXT = "Your bank account has been blocked. Call now and share your OTP."


class RecordingStub(StubClassifier):
    """Stub that records how it was called, so the tests can assert on the
    number and kind of model calls rather than on timing."""

    def __init__(self) -> None:
        self.verdict_calls: list[bool] = []  # one entry per complete(), value = brief?
        self.rationale_calls = 0
        self.first_pass_messages: list[dict[str, str]] | None = None
        self.rationale_messages: list[dict[str, str]] | None = None

    def complete(self, messages, *, brief: bool = False) -> str:
        self.verdict_calls.append(brief)
        self.first_pass_messages = list(messages)
        return super().complete(messages, brief=brief)

    def complete_text(self, messages, *, max_tokens: int = 80) -> str:
        self.rationale_calls += 1
        self.rationale_messages = list(messages)
        return super().complete_text(messages, max_tokens=max_tokens)


def _classify(policy, classifier, text: str, *, two_pass: bool = True):
    return classify(
        text,
        policy=policy,
        classifier=classifier,
        hold_threshold=HOLD_T,
        block_threshold=BLOCK_T,
        two_pass=two_pass,
    )


def test_allowed_message_costs_one_call_and_no_rationale(sample_policy) -> None:
    """The 85-95% case: one brief call, no rationale pass at all."""
    stub = RecordingStub()
    decision = _classify(sample_policy, stub, ALLOW_TEXT)

    assert decision.action is ModerationAction.ALLOW
    assert stub.verdict_calls == [True]  # one call, brief
    assert stub.rationale_calls == 0
    assert decision.nudge_text is None


@pytest.mark.parametrize(
    ("text", "action"),
    [
        (NUDGE_TEXT, ModerationAction.NUDGE),
        (HOLD_TEXT, ModerationAction.HOLD),
        (BLOCK_TEXT, ModerationAction.BLOCK),
    ],
)
def test_non_allowed_message_gets_a_second_pass(sample_policy, text, action) -> None:
    """Anything a human will read pays for a rationale -- and only those."""
    stub = RecordingStub()
    decision = _classify(sample_policy, stub, text)

    assert decision.action is action
    assert stub.verdict_calls == [True]
    assert stub.rationale_calls == 1
    assert "[stub] rationale" in decision.rationale
    assert decision.nudge_text  # sender is still told


def test_rationale_pass_continues_the_first_pass_conversation(sample_policy) -> None:
    """Pass 2 must *extend* pass 1's messages, not start a new prompt.

    This is the whole cost argument: llama.cpp caches a prompt prefix, so
    appending turns evaluates only the new tokens, while a fresh system
    prompt re-evaluates the entire policy. Measured at roughly the cost of
    a second full classification (README, "Measured"). A refactor that
    rebuilds the prompt here would silently undo the optimisation and
    nothing else in the suite would notice -- hence this test.
    """
    stub = RecordingStub()
    _classify(sample_policy, stub, HOLD_TEXT)

    first = stub.first_pass_messages
    second = stub.rationale_messages
    assert first is not None and second is not None
    assert second[: len(first)] == first, "pass 2 did not reuse pass 1's prefix verbatim"
    # Exactly two appended turns: the model's own verdict, then the ask.
    assert len(second) == len(first) + 2
    assert second[len(first)]["role"] == "assistant"
    assert second[-1]["role"] == "user"
    # The sender's text is in the cached prefix already; repeating it would
    # both cost tokens and give the injection a second bite.
    assert HOLD_TEXT not in second[-1]["content"]


def test_two_pass_does_not_change_the_decision_for_a_deterministic_backend(
    sample_policy,
) -> None:
    """Same label, action and confidence with the feature on and off.

    **This holds for a deterministic backend only, and does not generalise
    to a real model.** The brief prompt asks for a different output shape
    than the full one, so a real model's confidence can shift and cross a
    threshold. Measured on the pinned model, 2026-09-24: 2 of 26 messages
    decided differently between the two modes (see README, "Measured").
    Both happened to move toward the safer label, but that is an
    observation on a small, non-independent set -- not a guarantee.

    So what this test actually pins is narrower than it looks: the
    *plumbing* of two-pass introduces no divergence of its own. Any real
    divergence comes from the model, and is measured, not asserted.
    """
    for text in (ALLOW_TEXT, NUDGE_TEXT, HOLD_TEXT, BLOCK_TEXT):
        one = _classify(sample_policy, RecordingStub(), text, two_pass=False)
        two = _classify(sample_policy, RecordingStub(), text, two_pass=True)
        assert (one.label, one.action, one.confidence) == (two.label, two.action, two.confidence), (
            f"two-pass changed the verdict for {text!r}"
        )


def test_single_pass_mode_makes_one_unbrief_call(sample_policy) -> None:
    stub = RecordingStub()
    _classify(sample_policy, stub, HOLD_TEXT, two_pass=False)
    assert stub.verdict_calls == [False]
    assert stub.rationale_calls == 0


def test_gate_note_survives_the_rationale_pass(sample_policy) -> None:
    """When the gate downgrades an action it appends a note explaining why.
    The second pass rewrites the rationale, so that note must be carried
    over -- a moderator needs to know the action was downgraded."""
    stub = RecordingStub()
    # STUB_UNSURE_TOKEN forces confidence 0.40, below the hold threshold,
    # so a would-be ALLOW becomes HOLD with a gating note.
    decision = _classify(sample_policy, stub, f"{ALLOW_TEXT} [[stub:unsure]]")

    assert decision.action is ModerationAction.HOLD
    assert stub.rationale_calls == 1
    assert "below hold threshold" in decision.rationale
    assert "[stub] rationale" in decision.rationale


def test_backend_failure_in_pass_one_still_fails_closed(sample_policy) -> None:
    stub = RecordingStub()
    decision = _classify(sample_policy, stub, "anything [[stub:fail]]")

    assert decision.action is ModerationAction.HOLD
    assert decision.confidence == 0.0
    assert decision.degraded.active
    assert decision.degraded.reason is DegradedReason.MODEL_FALLBACK
    assert stub.rationale_calls == 0


def test_unparseable_pass_one_fails_closed(sample_policy) -> None:
    stub = RecordingStub()
    decision = _classify(sample_policy, stub, "anything [[stub:garbage]]")

    assert decision.action is ModerationAction.HOLD
    assert decision.degraded.active
    assert "unparseable" in decision.degraded.detail
    assert stub.rationale_calls == 0


def test_rationale_failure_keeps_the_verdict(sample_policy) -> None:
    """A failed *explanation* must not discard a sound classification."""

    class RationaleFails(RecordingStub):
        def complete_text(self, messages, *, max_tokens: int = 80) -> str:
            self.rationale_calls += 1
            raise ClassifierError("rationale backend exploded")

    stub = RationaleFails()
    decision = _classify(sample_policy, stub, BLOCK_TEXT)

    assert decision.action is ModerationAction.BLOCK  # verdict preserved
    assert decision.label is ModerationLabel.E_HARMFUL
    assert not decision.degraded.active  # the verdict itself was fine
    assert "rationale pass failed" in decision.rationale
    assert stub.rationale_calls == 1


def test_empty_rationale_leaves_the_pass_one_rationale(sample_policy) -> None:
    class EmptyRationale(RecordingStub):
        def complete_text(self, messages, *, max_tokens: int = 80) -> str:
            self.rationale_calls += 1
            return "   "

    stub = EmptyRationale()
    decision = _classify(sample_policy, stub, HOLD_TEXT)
    assert decision.action is ModerationAction.HOLD
    assert decision.rationale  # something is always there for the moderator
