import pytest
from contracts.ai.common import DegradedReason
from contracts.ai.language import LanguageCode
from contracts.ai.moderation import ModerationAction, ModerationDecision, ModerationLabel
from services.ai.moderation.decide import decide
from services.ai.moderation.prompt import RawVerdict

HOLD_T = 0.70
BLOCK_T = 0.85


def _decide(verdict, policy, **kw) -> ModerationDecision:
    return decide(
        verdict,
        policy=policy,
        model_version="test@0",
        hold_threshold=HOLD_T,
        block_threshold=BLOCK_T,
        **kw,
    )


@pytest.mark.parametrize(
    ("label", "confidence", "expected"),
    [
        (ModerationLabel.A_DEVOTIONAL, 0.95, ModerationAction.ALLOW),
        (ModerationLabel.B_ORGANIZATIONAL, 0.71, ModerationAction.ALLOW),
        (ModerationLabel.C_PERSONAL, 0.80, ModerationAction.NUDGE),
        (ModerationLabel.D_DISPUTATIONAL, 0.99, ModerationAction.HOLD),
        (ModerationLabel.E_HARMFUL, 0.90, ModerationAction.BLOCK),
        (ModerationLabel.E_HARMFUL, 0.85, ModerationAction.BLOCK),  # at the threshold
    ],
)
def test_confident_verdicts_take_the_labels_default_action(
    sample_policy, label, confidence, expected
) -> None:
    d = _decide(RawVerdict(label, confidence, "r"), sample_policy)
    assert d.label is label
    assert d.action is expected
    assert d.confidence == confidence
    assert d.policy_version == "policy@2026-10-01"
    assert d.model_version == "test@0"
    assert not d.degraded.active


@pytest.mark.parametrize(
    "label",
    [
        ModerationLabel.A_DEVOTIONAL,
        ModerationLabel.B_ORGANIZATIONAL,
        ModerationLabel.C_PERSONAL,
        ModerationLabel.E_HARMFUL,
    ],
)
def test_below_hold_threshold_nothing_happens_on_the_models_word(sample_policy, label) -> None:
    d = _decide(RawVerdict(label, 0.69, "unsure"), sample_policy)
    assert d.action is ModerationAction.HOLD
    assert d.label is label  # the guess is kept for the moderator to see
    assert "below hold threshold" in d.rationale
    assert d.nudge_text  # the sender is told it is waiting


def test_block_needs_the_higher_threshold(sample_policy) -> None:
    d = _decide(RawVerdict(ModerationLabel.E_HARMFUL, 0.80, "probably abuse"), sample_policy)
    assert d.action is ModerationAction.HOLD
    assert "below block threshold" in d.rationale


def test_allow_never_carries_a_notice(sample_policy) -> None:
    d = _decide(RawVerdict(ModerationLabel.A_DEVOTIONAL, 0.9, "r"), sample_policy)
    assert d.nudge_text is None
    assert d.nudge_language is None


def test_every_non_allow_carries_the_policys_notice(sample_policy) -> None:
    nudge = _decide(RawVerdict(ModerationLabel.C_PERSONAL, 0.9, "r"), sample_policy)
    hold = _decide(RawVerdict(ModerationLabel.D_DISPUTATIONAL, 0.9, "r"), sample_policy)
    block = _decide(RawVerdict(ModerationLabel.E_HARMFUL, 0.9, "r"), sample_policy)
    assert "(sample)" in nudge.nudge_text
    assert "waiting for a volunteer" in hold.nudge_text
    assert "was not sent" in block.nudge_text
    for d in (nudge, hold, block):
        assert d.nudge_language is LanguageCode.ENGLISH
        assert (
            "delete" not in d.nudge_text.lower()
            or "nothing has been deleted" in d.nudge_text.lower()
        )


def test_no_verdict_fails_closed_to_a_human(sample_policy) -> None:
    d = _decide(None, sample_policy, failure_detail="timeout after 20s")
    assert d.action is ModerationAction.HOLD
    assert d.confidence == 0.0
    assert d.degraded.active
    assert d.degraded.reason is DegradedReason.MODEL_FALLBACK
    assert "timeout after 20s" in d.degraded.detail
    assert "held for human review" in d.rationale
    assert d.nudge_text  # sender still told it is waiting, never silently dropped
    ModerationDecision.model_validate(d.model_dump())  # still a valid contract payload


def test_thresholds_are_respected_when_changed(sample_policy) -> None:
    strict = decide(
        RawVerdict(ModerationLabel.A_DEVOTIONAL, 0.90, "r"),
        policy=sample_policy,
        model_version="t",
        hold_threshold=0.95,
        block_threshold=0.99,
    )
    assert strict.action is ModerationAction.HOLD
