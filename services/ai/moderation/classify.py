"""
One message in, one ModerationDecision out -- including the two-pass
optimisation.

Why this exists as its own step rather than inside app.py's route: the
*gate* (decide.py) is what determines whether a rationale is needed, so
pass 1, the gate, and the conditional pass 2 have to run together. Keeping
them in one function means app.py submits one unit of work holding one
semaphore slot under one timeout, instead of trying to account for two
model calls separately.

Two-pass, and why it is worth it (README, "Measured", 2026-09-23):

    single pass   ~10.3 s p50   -- the model writes a 40-60 token rationale
                                   for every message, including the ones
                                   nobody will ever read
    two-pass      pass 1 asks for {label, confidence} only (~12-24 tokens);
                  pass 2 writes the rationale, and runs ONLY when the gated
                  action is not ALLOW

The proposal (section 7.3) expects 85-95% of traffic to auto-allow, so most
messages pay pass 1 alone. A held or blocked message pays both -- which is
the right place to spend the time, because that is the message a human is
about to read.

Correctness is unchanged. The gate still decides; pass 2 is given the label
and cannot alter it (see prompt.build_rationale_followup), and every
failure path still ends in HOLD via decide(None, ...).

Pass 2 *continues pass 1's conversation* rather than starting a new one, so
llama.cpp reuses the cached prefix and only evaluates the few appended
tokens. Measured: a fresh system prompt for pass 2 cost about as much as a
whole extra classification, which made two-pass a net loss on non-ALLOW
traffic. See README's "Measured" for the A/B.
"""

from __future__ import annotations

import logging

from contracts.ai.moderation import ModerationAction, ModerationDecision
from services.ai.moderation.decide import decide
from services.ai.moderation.engine import Classifier, ClassifierError
from services.ai.moderation.policy import Policy
from services.ai.moderation.prompt import (
    build_messages,
    build_rationale_followup,
    parse_model_output,
)

logger = logging.getLogger("services.ai.moderation.classify")


def classify(
    text: str,
    *,
    policy: Policy,
    classifier: Classifier,
    hold_threshold: float,
    block_threshold: float,
    two_pass: bool = True,
    rationale_max_tokens: int = 80,
) -> ModerationDecision:
    """Classify one message and return the gated decision.

    Never raises for a model problem: a backend error, an unparseable
    reply, or a failed rationale pass all end in a decision, and any
    failure that leaves us without a verdict ends in HOLD.
    """
    common = {
        "policy": policy,
        "model_version": classifier.model_version,
        "hold_threshold": hold_threshold,
        "block_threshold": block_threshold,
    }

    # ---- pass 1: the verdict --------------------------------------------
    first_pass_messages = build_messages(policy, text, brief=two_pass)
    try:
        raw = classifier.complete(first_pass_messages, brief=two_pass)
    except ClassifierError as exc:
        logger.error("moderation backend error: %s", exc)
        return decide(None, failure_detail=f"backend error: {exc}", **common)

    verdict = parse_model_output(raw)
    if verdict is None:
        logger.warning("unparseable model output (%d chars)", len(raw))
        return decide(None, failure_detail="unparseable model output", **common)

    decision = decide(verdict, **common)

    # ---- pass 2: the rationale, only where a human will read it ---------
    if not two_pass or decision.action is ModerationAction.ALLOW:
        return decision

    try:
        rationale = classifier.complete_text(
            build_rationale_followup(first_pass_messages, raw, decision.label, policy),
            max_tokens=rationale_max_tokens,
        ).strip()
    except ClassifierError as exc:
        # The verdict itself is sound -- only the explanation failed. Keep
        # the decision and say so, rather than throwing away a good
        # classification because its prose did not generate.
        logger.warning("rationale pass failed, keeping verdict: %s", exc)
        return decision.model_copy(
            update={"rationale": f"{decision.rationale} [rationale pass failed: {exc}]"}
        )

    if not rationale:
        return decision

    # decide() may have appended a gating note to the model's pass-1
    # rationale (e.g. "confidence below block threshold"). That note is
    # about the *decision*, not the message, so it must survive here --
    # the moderator needs to know the action was downgraded.
    gate_note = ""
    if "[" in decision.rationale:
        gate_note = " " + decision.rationale[decision.rationale.index("[") :]
    return decision.model_copy(update={"rationale": f"{rationale[:500]}{gate_note}"})
