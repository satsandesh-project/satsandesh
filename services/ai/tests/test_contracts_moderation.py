from typing import Any

import pytest
from contracts.ai.language import LanguageCode
from contracts.ai.moderation import (
    ModerationAction,
    ModerationDecision,
    ModerationLabel,
    ModerationRequest,
)
from pydantic import ValidationError


def _base_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "label": ModerationLabel.A_DEVOTIONAL,
        "confidence": 0.9,
        "action": ModerationAction.ALLOW,
        "rationale": "looks fine",
        "policy_version": "policy@2026-08-13",
        "model_version": "qwen2.5-7b-instruct-q4",
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize("label", list(ModerationLabel))
def test_all_labels_round_trip(label: ModerationLabel) -> None:
    decision = ModerationDecision(**_base_kwargs(label=label))
    restored = ModerationDecision.model_validate_json(decision.model_dump_json())
    assert restored.label is label


@pytest.mark.parametrize("action", list(ModerationAction))
def test_all_actions_round_trip(action: ModerationAction) -> None:
    decision = ModerationDecision(**_base_kwargs(action=action))
    assert decision.action is action


def test_confidence_must_be_within_unit_interval() -> None:
    with pytest.raises(ValidationError):
        ModerationDecision(**_base_kwargs(confidence=1.5))


def test_hold_is_expressible_regardless_of_label_or_confidence() -> None:
    """Low-confidence classifications must be able to route to HOLD no matter what
    label the classifier guessed."""
    decision = ModerationDecision(
        **_base_kwargs(
            label=ModerationLabel.E_HARMFUL, confidence=0.12, action=ModerationAction.HOLD
        )
    )
    assert decision.action is ModerationAction.HOLD


def test_nudge_text_can_be_in_a_different_language_than_the_rationale() -> None:
    """The rationale is for internal/ops reading (English pivot); the nudge is
    sender-facing and must be rendered in the sender's own language."""
    decision = ModerationDecision(
        **_base_kwargs(
            label=ModerationLabel.D_DISPUTATIONAL,
            action=ModerationAction.NUDGE,
            rationale="Message reads as argumentative; nudging sender toward gentler phrasing.",
            nudge_text="దయచేసి మృదువుగా చెప్పండి",
            nudge_language=LanguageCode.TELUGU,
        )
    )
    assert decision.nudge_language is LanguageCode.TELUGU
    assert decision.nudge_text != decision.rationale


def test_nudge_fields_default_to_none_when_no_nudge_is_produced() -> None:
    decision = ModerationDecision(**_base_kwargs())
    assert decision.nudge_text is None
    assert decision.nudge_language is None


def test_moderation_request_minimal() -> None:
    req = ModerationRequest(text="Just checking on the satsang timing")
    assert req.text
