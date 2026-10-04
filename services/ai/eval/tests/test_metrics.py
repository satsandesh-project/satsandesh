"""
The scoring rules, which are the part that can be quietly wrong.

A harness that miscounts is worse than no harness: it produces a number
that looks like evidence. These tests pin the definitions the proposal
cares about rather than the plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from services.ai.eval.metrics import Rate, score_adequacy, score_moderation
from services.ai.eval.schema import (
    AdequacyItem,
    AdequacyRating,
    EvalSet,
    ModerationEvalItem,
    Provenance,
)

NOW = datetime.now(UTC)


def _mod(gold: str, action: str | None, *, label: str | None = None, **kw) -> ModerationEvalItem:
    return ModerationEvalItem(
        item_id=kw.pop("item_id", f"m{gold}{action}"),
        provenance=kw.pop("provenance", Provenance.PILOT),
        text="...",
        gold_label=gold,
        gold_labelled_by="E01",
        predicted_label=label or (gold if action else None),
        predicted_action=action,
        **kw,
    )


# -- the two directional rates --------------------------------------------


def test_a_held_devotional_message_is_a_false_hold() -> None:
    s = score_moderation([_mod("A", "HOLD")])
    assert s.false_hold.numerator == 1
    assert str(s.false_hold) == "1/1 (100.0%)"


def test_a_nudged_or_blocked_deliverable_message_also_counts_as_a_false_hold() -> None:
    """From the sender's side, 'my message did not arrive' is the same harm
    whichever action caused it -- so NUDGE and BLOCK count too, not only
    HOLD."""
    s = score_moderation([_mod("A", "NUDGE"), _mod("B", "BLOCK"), _mod("A", "ALLOW")])
    assert s.false_hold.numerator == 2
    assert s.false_hold.denominator == 3


def test_holding_a_personal_message_is_not_a_false_hold() -> None:
    """C and D are not meant to reach the circle, so withholding them is
    the filter working, not a defect."""
    s = score_moderation([_mod("C", "NUDGE"), _mod("D", "HOLD")])
    assert s.false_hold.denominator == 0, "neither is a deliverable message"
    assert s.false_hold.value is None


def test_allowing_a_harmful_message_is_missed_harm() -> None:
    s = score_moderation([_mod("E", "ALLOW"), _mod("E", "BLOCK")])
    assert s.missed_harm.numerator == 1
    assert s.missed_harm.denominator == 2


# -- things that make a number dishonest ----------------------------------


def test_an_empty_sample_has_no_rate_rather_than_zero_percent() -> None:
    """0% would read as success. There is no rate for nothing."""
    assert Rate(0, 0).value is None
    assert "no items" in str(Rate(0, 0))


def test_unclassified_items_are_counted_not_silently_dropped() -> None:
    """A run that failed on a third of the set must not look clean on the
    rest."""
    s = score_moderation([_mod("A", "ALLOW"), _mod("A", None), _mod("E", None)])
    assert s.scored == 1
    assert s.unscored == 2


def test_provenance_filtering_keeps_synthetic_out_of_a_baseline() -> None:
    """Synthetic rows were written by the same people who wrote the policy;
    mixing them into a pilot baseline flatters it."""
    items = [
        _mod("A", "HOLD", item_id="s1", provenance=Provenance.SYNTHETIC),
        _mod("A", "ALLOW", item_id="p1", provenance=Provenance.PILOT),
    ]
    assert score_moderation(items, only=Provenance.PILOT).false_hold.numerator == 0
    assert score_moderation(items, only=Provenance.SYNTHETIC).false_hold.numerator == 1
    assert score_moderation(items).false_hold.denominator == 2, "unfiltered mixes them"


def test_a_degraded_verdict_is_tracked_separately() -> None:
    """A fail-closed hold is not the classifier judging badly -- it is the
    classifier not answering. Counting those as decisions would make an
    outage look like an opinion."""
    s = score_moderation([_mod("A", "HOLD", degraded=True), _mod("A", "ALLOW")])
    assert s.degraded.numerator == 1


# -- adequacy -------------------------------------------------------------


def _adequacy(src: str, tgt: str, *verdicts: bool, item_id: str = "a1") -> AdequacyItem:
    return AdequacyItem(
        item_id=item_id,
        provenance=Provenance.PILOT,
        source_language=src,
        target_language=tgt,
        source_text="...",
        rendered_text="...",
        ratings=[
            AdequacyRating(rated_by=f"V{n}", meaning_preserved=v, rated_at=NOW)
            for n, v in enumerate(verdicts)
        ],
    )


def test_adequacy_is_reported_per_language_pair_not_averaged() -> None:
    """An average would let a strong te->en number hide a weak en->hi one."""
    s = EvalSet(
        name="t",
        description="",
        created_at=NOW,
        adequacy=[
            _adequacy("te", "en", True, True, item_id="a1"),
            _adequacy("en", "hi", False, False, item_id="a2"),
        ],
    )
    rows = {r.pair: r for r in score_adequacy(s)}
    assert rows["te->en"].preserved.numerator == 1
    assert rows["en->hi"].preserved.numerator == 0


def test_rater_disagreement_is_reported_not_resolved_by_majority() -> None:
    """Two bilingual readers disagreeing is itself a finding about the
    translation, not a tie to break."""
    s = EvalSet(
        name="t", description="", created_at=NOW, adequacy=[_adequacy("te", "en", True, False)]
    )
    row = score_adequacy(s)[0]
    assert row.disagreed == 1
    assert row.preserved.denominator == 0, "a disagreed item scores neither way"


def test_unrated_renderings_are_visible() -> None:
    s = EvalSet(
        name="t",
        description="",
        created_at=NOW,
        adequacy=[_adequacy("te", "en"), _adequacy("te", "en", True, item_id="a2")],
    )
    assert score_adequacy(s)[0].unrated == 1


# -- the privacy guard ----------------------------------------------------


def test_a_participant_name_is_rejected_where_a_code_belongs() -> None:
    """These files are committed and the repository goes public in Week 12.
    The most likely 'not a code' is a real person's name."""
    with pytest.raises(ValidationError):
        _mod("A", "ALLOW", participant_code="Lakshmi")
    assert _mod("A", "ALLOW", participant_code="E07").participant_code == "E07"
