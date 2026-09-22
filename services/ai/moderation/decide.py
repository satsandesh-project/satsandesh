"""
Turn a model verdict (or the absence of one) into a ModerationDecision.

This is the part of the classifier that is *not* the model, and it is pure
so it can be tested exhaustively without weights. The rules, from the
proposal (section 7.3) and docs/security-checklist.md (B5):

  - The label's default action comes from the policy table (A/B allow,
    C nudge, D hold, E block).
  - Below `hold_threshold` nothing happens on the model's word: the action
    becomes HOLD, whatever the label. An unsure allow is as wrong as an
    unsure block -- it just fails in the other direction.
  - BLOCK additionally requires `block_threshold`. A wrong block is the most
    visible failure the product can have ("my message disappeared"), so it
    needs the most certainty. Below it, HOLD: a human decides within the SLA.
  - No verdict at all (model error, timeout, unparseable output) is HOLD with
    `degraded` set -- fail closed to a human, never open.
  - Nothing is ever deleted silently: every non-ALLOW carries a sender
    notice from the policy document.
"""

from __future__ import annotations

from contracts.ai.common import DegradedMode, DegradedReason
from contracts.ai.language import LanguageCode
from contracts.ai.moderation import ModerationAction, ModerationDecision, ModerationLabel
from services.ai.moderation.policy import Policy
from services.ai.moderation.prompt import RawVerdict

# Which language the notice text is in. The policy document's notices are
# English masters; ModerationRequest carries no sender language yet (see
# README, "Open questions"), so the orchestrator translates downstream.
_NOTICE_LANGUAGE = LanguageCode.ENGLISH


def decide(
    verdict: RawVerdict | None,
    *,
    policy: Policy,
    model_version: str,
    hold_threshold: float,
    block_threshold: float,
    failure_detail: str | None = None,
) -> ModerationDecision:
    if verdict is None:
        # Fail closed. The label is a placeholder the console shows as
        # "unclassified -- held"; confidence 0.0 and `degraded` make that
        # unambiguous to any consumer.
        return _with_notice(
            ModerationDecision(
                label=ModerationLabel.D_DISPUTATIONAL,
                confidence=0.0,
                action=ModerationAction.HOLD,
                rationale=(
                    "Classifier produced no usable verdict; held for human review. "
                    + (failure_detail or "")
                ).strip(),
                policy_version=policy.version,
                model_version=model_version,
                degraded=DegradedMode(
                    active=True,
                    reason=DegradedReason.MODEL_FALLBACK,
                    detail=failure_detail or "no verdict",
                ),
            ),
            policy,
        )

    default_action = policy.action_for(verdict.label)
    action = default_action
    rationale = verdict.rationale

    if verdict.confidence < hold_threshold:
        if action is not ModerationAction.HOLD:
            action = ModerationAction.HOLD
            rationale = (
                f"{verdict.rationale} [confidence {verdict.confidence:.2f} below "
                f"hold threshold {hold_threshold:.2f}; held for a human instead of "
                f"{default_action.value}]"
            )
    elif action is ModerationAction.BLOCK and verdict.confidence < block_threshold:
        action = ModerationAction.HOLD
        rationale = (
            f"{verdict.rationale} [confidence {verdict.confidence:.2f} below block "
            f"threshold {block_threshold:.2f}; held for a human instead of BLOCK]"
        )

    return _with_notice(
        ModerationDecision(
            label=verdict.label,
            confidence=verdict.confidence,
            action=action,
            rationale=rationale,
            policy_version=policy.version,
            model_version=model_version,
            degraded=DegradedMode.ok(),
        ),
        policy,
    )


def _with_notice(decision: ModerationDecision, policy: Policy) -> ModerationDecision:
    notice = policy.notice_for(decision.action)
    if notice is None:
        return decision
    return decision.model_copy(update={"nudge_text": notice, "nudge_language": _NOTICE_LANGUAGE})
