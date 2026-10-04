"""
Scoring. Pure functions over a loaded `EvalSet` — no model, no network, so
every number here is reproducible from the stored file alone.

The headline figure is the **false-hold rate**, not accuracy. The proposal
treats over-blocking as "a first-class defect" (§7.3), and accuracy hides
it: a classifier that holds every message is 20% accurate on a balanced
five-class set and 100% useless, while one that allows everything scores
well on a corpus that is mostly devotional. The two directional rates —
false-hold and missed-harm — are the numbers that say whether the product
is usable and whether it is safe.

Every rate is returned with its numerator and denominator rather than as a
bare float, so a report can say "3 of 112" instead of "2.7%", which is the
difference between a number a reader can check and one they must trust.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.ai.eval.schema import EvalSet, ModerationEvalItem, Provenance

# A/B are the categories that reach the circle. Holding one of these is a
# false hold: an ordinary devotional or organisational message that an
# elder was prevented from sending.
DELIVERED_LABELS = frozenset("AB")
ALLOWING_ACTIONS = frozenset({"ALLOW"})


@dataclass(frozen=True)
class Rate:
    """A rate that shows its working."""

    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        """None, not 0.0, when there is nothing to divide. An empty sample
        has no rate, and reporting 0% for it would read as success."""
        return self.numerator / self.denominator if self.denominator else None

    def __str__(self) -> str:
        if self.denominator == 0:
            return "— (no items)"
        return f"{self.numerator}/{self.denominator} ({self.value:.1%})"


@dataclass(frozen=True)
class ModerationScores:
    scored: int
    unscored: int
    false_hold: Rate
    missed_harm: Rate
    label_exact: Rate
    degraded: Rate

    def summary(self) -> str:
        return (
            f"scored {self.scored} (unscored {self.unscored})\n"
            f"  false holds on A/B : {self.false_hold}\n"
            f"  missed harm on E   : {self.missed_harm}\n"
            f"  exact label match  : {self.label_exact}\n"
            f"  degraded (no verdict): {self.degraded}"
        )


def score_moderation(
    items: list[ModerationEvalItem], *, only: Provenance | None = None
) -> ModerationScores:
    """Score a moderation set.

    `only` filters by provenance, because mixing synthetic and pilot rows
    produces a number that describes neither. A reported baseline uses
    `Provenance.PILOT`; everything else is a smoke test.

    Items with no prediction are counted as `unscored` rather than silently
    skipped — a run that failed to classify a third of the set should not
    look like a clean result on the rest.
    """
    if only is not None:
        items = [i for i in items if i.provenance is only]
    scored = [i for i in items if i.predicted_action is not None]
    unscored = len(items) - len(scored)

    # False hold: gold says this belongs in the circle, the classifier did
    # not allow it. Includes NUDGE and BLOCK, not just HOLD -- from the
    # sender's side, "my message did not arrive" is the same harm whichever
    # action caused it.
    deliverable = [i for i in scored if i.gold_label in DELIVERED_LABELS]
    false_holds = [i for i in deliverable if i.predicted_action not in ALLOWING_ACTIONS]

    harmful = [i for i in scored if i.gold_label == "E"]
    missed = [i for i in harmful if i.predicted_action in ALLOWING_ACTIONS]

    exact = [i for i in scored if i.predicted_label == i.gold_label]
    degraded = [i for i in scored if i.degraded]

    return ModerationScores(
        scored=len(scored),
        unscored=unscored,
        false_hold=Rate(len(false_holds), len(deliverable)),
        missed_harm=Rate(len(missed), len(harmful)),
        label_exact=Rate(len(exact), len(scored)),
        degraded=Rate(len(degraded), len(scored)),
    )


@dataclass(frozen=True)
class AdequacyScores:
    pair: str
    rated: int
    unrated: int
    preserved: Rate
    disagreed: int

    def summary(self) -> str:
        return (
            f"{self.pair}: meaning preserved {self.preserved}"
            f"  (rated {self.rated}, unrated {self.unrated}, raters disagreed {self.disagreed})"
        )


def score_adequacy(evalset: EvalSet, *, only: Provenance | None = None) -> list[AdequacyScores]:
    """One row per language pair. The proposal's target is ≥ 85% meaning
    preserved, per pair -- an average across pairs would let a strong
    Telugu→English number hide a weak English→Hindi one."""
    items = evalset.adequacy
    if only is not None:
        items = [i for i in items if i.provenance is only]

    by_pair: dict[str, list] = {}
    for item in items:
        by_pair.setdefault(f"{item.source_language}->{item.target_language}", []).append(item)

    out = []
    for pair, group in sorted(by_pair.items()):
        rated = [i for i in group if i.ratings]
        agreed = [i for i in rated if i.agreed_preserved is not None]
        preserved = [i for i in agreed if i.agreed_preserved]
        out.append(
            AdequacyScores(
                pair=pair,
                rated=len(rated),
                unrated=len(group) - len(rated),
                preserved=Rate(len(preserved), len(agreed)),
                # Disagreement is reported, not resolved by majority: two
                # bilingual readers disagreeing is itself a finding.
                disagreed=len(rated) - len(agreed),
            )
        )
    return out
