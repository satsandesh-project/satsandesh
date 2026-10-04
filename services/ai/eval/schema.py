"""
The shapes the evaluation sets are stored in.

These are *evaluation* records, not wire contracts: they never travel
between services, so they live here rather than in `contracts/`. They are
pydantic models anyway, because the thing that matters about an eval set
is that a half-filled row fails loudly rather than being silently averaged
into a score.

Two rules enforced here rather than left to discipline:

1. **A rating carries its rater.** An adequacy score with no
   `rated_by` is not evidence; it is a number somebody typed. The same
   goes for a gold label on a moderation item.
2. **Nothing identifying.** These files are committed, and the repository
   goes public under Apache-2.0 in Week 12. A row carries a participant
   *code* (`E07`), never a name, never a phone number, and the
   code -> identity sheet lives outside this repository
   (`docs/research/interview-consent.md` Part D).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# `E01`, `E12` -- the participant code scheme from the consent form. A row
# that carries anything else is rejected, because the most likely "anything
# else" is a real name.
PARTICIPANT_CODE = re.compile(r"^E\d{2,3}$")

SCHEMA_VERSION = "1"
"""Bumped when a field is added, renamed or retyped. A stored set records
the version it was written under, so a later harness can tell whether it
is reading a shape it understands rather than guessing."""


class Provenance(str, Enum):
    """Where a row came from. Mixing these in one score is the easiest way
    to produce a number that means nothing."""

    SYNTHETIC = "synthetic"
    """Invented by the team (docs/policy-taxonomy-workshop.md). Fine for
    smoke-testing the harness; NOT a baseline, because the messages were
    written by the same people who wrote the policy."""

    PILOT = "pilot"
    """Drawn from consented pilot traffic. The only provenance a reported
    baseline may use (proposal §13)."""

    REDTEAM = "redteam"
    """Written during a red-team round to defeat the filter (Week 9).
    Scored separately: a filter that catches 100% of deliberate attacks and
    holds half of ordinary devotional speech is not a good filter."""


class AdequacyRating(BaseModel):
    """One bilingual volunteer's judgement of one rendering.

    `meaning_preserved` is deliberately a three-way choice rather than a
    1-5 scale. The proposal's target is "≥ 85% of pilot voice notes
    *meaning preserved*", which is a yes/no question; a 5-point scale
    invites a 3 and turns the target into an argument about where the line
    sits.
    """

    rated_by: str = Field(min_length=1, description="Volunteer's code, not their name.")
    meaning_preserved: bool
    partially: bool = Field(
        default=False,
        description="The gist survived but a detail did not. Counted as NOT preserved for "
        "the headline figure, and kept separately so 'nearly right' and 'wrong' do not "
        "collapse into one number.",
    )
    note: str | None = Field(default=None, max_length=500)
    rated_at: datetime


class AdequacyItem(BaseModel):
    """One rendering, and what raters made of it. 50 of these per language
    pair is the Week-8 sample."""

    item_id: str
    provenance: Provenance
    source_language: str
    target_language: str
    source_text: str = Field(min_length=1, description="As the sender wrote or said it.")
    pivot_text_en: str | None = None
    rendered_text: str = Field(min_length=1, description="What the receiver was shown.")
    participant_code: str | None = Field(
        default=None, description="Whose message this was, as a code. Null for synthetic rows."
    )
    ratings: list[AdequacyRating] = Field(default_factory=list)

    @field_validator("participant_code")
    @classmethod
    def _code_not_a_name(cls, value: str | None) -> str | None:
        if value is not None and not PARTICIPANT_CODE.match(value):
            raise ValueError(
                f"participant_code must look like 'E07', got {value!r} -- these files are "
                "committed and the repository goes public; identities live in the "
                "supervisor's mapping sheet, not here"
            )
        return value

    @property
    def agreed_preserved(self) -> bool | None:
        """True/False only when raters agree. None means disagreement, which
        is a real outcome worth seeing rather than a tie to break by
        majority: two bilingual readers disagreeing about whether meaning
        survived is itself a finding about the translation."""
        if not self.ratings:
            return None
        verdicts = {r.meaning_preserved for r in self.ratings}
        return verdicts.pop() if len(verdicts) == 1 else None


class ModerationEvalItem(BaseModel):
    """One message, the label a human says is correct, and what the
    classifier actually did.

    `gold_label` is what a *person* decided. `predicted_*` is filled by a
    harness run. Keeping them in one row means a scored set is
    self-contained: you can recompute every metric from the file without
    needing the run that produced it.
    """

    item_id: str
    provenance: Provenance
    text: str = Field(min_length=1, description="The English pivot the classifier reads.")
    source_language: str | None = None
    participant_code: str | None = None

    gold_label: str = Field(pattern=r"^[A-E]$")
    gold_labelled_by: str = Field(
        min_length=1, description="Who decided. A gold label with no author is an opinion."
    )
    gold_note: str | None = Field(default=None, max_length=500)

    predicted_label: str | None = Field(default=None, pattern=r"^[A-E]$")
    predicted_action: str | None = None
    predicted_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    policy_version: str | None = None
    model_version: str | None = None
    degraded: bool = False

    @field_validator("participant_code")
    @classmethod
    def _code_not_a_name(cls, value: str | None) -> str | None:
        if value is not None and not PARTICIPANT_CODE.match(value):
            raise ValueError(f"participant_code must look like 'E07', got {value!r}")
        return value


class EvalSet(BaseModel):
    """A stored, versioned set. `name` is how a report refers to it."""

    schema_version: str = SCHEMA_VERSION
    name: str
    description: str
    created_at: datetime
    adequacy: list[AdequacyItem] = Field(default_factory=list)
    moderation: list[ModerationEvalItem] = Field(default_factory=list)

    def provenances(self) -> set[Provenance]:
        return {i.provenance for i in self.adequacy} | {i.provenance for i in self.moderation}
