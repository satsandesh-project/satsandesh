"""
The moderator console's wire contract: the review queue, a human's
release/block decision, and the append-only audit trail behind both.

Scope. This package describes what the **gateway** serves to the console
(`clients/admin-console/`). It is not the classifier's contract — that is
`contracts/ai/moderation.py`, which answers "what does this text look
like?" and knows nothing about messages, circles or who reviewed what. The
split matters: the AI service classifies a string, the gateway records a
decision about a message and who made it.

Deliberately duplicated enums, not imports. `ModerationLabel` and
`ModerationAction` below mirror `contracts.ai.moderation`'s values
one-for-one, and are still defined here rather than imported — the blanket
"contracts/chat/ does not import contracts/ai/" rule (DECISIONS.md #5), for
the same reason `AudioFormat` and `MessageStatus` are duplicated in
common.py. The two packages are owned by different people on different
schedules; an import would make an AI-lane enum change a chat-lane wire
break. Drift is guarded by a test that compares the two value sets, not by
coupling.

Append-only. Nothing in this file describes editing or deleting a decision.
A release, a block, and a later reversal are each a new `ModerationEvent`;
the message's current state is `MessageStatus`, and this trail is the
history that explains it. The proposal (§7.3, §15) requires a full audit
trail and no silent deletion, and `docs/security-checklist.md` B5 asks for
the application's DB role to hold INSERT/SELECT on that table and nothing
else.

Status: proposed by M4 for issue #65, ahead of Week 7's console. The
gateway side (table, migration, routes) is M2's; this is the shape to
agree on before either side builds.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from contracts.chat.common import MediaRef, VersionedModel
from contracts.chat.renderings import LANGUAGE_PATTERN


class ModerationLabel(str, Enum):
    """The policy taxonomy (docs/policy-taxonomy.md). Values mirror
    `contracts.ai.moderation.ModerationLabel` exactly — see this module's
    docstring for why they are duplicated rather than imported."""

    A_DEVOTIONAL = "A_DEVOTIONAL"
    B_ORGANIZATIONAL = "B_ORGANIZATIONAL"
    C_PERSONAL = "C_PERSONAL"
    D_DISPUTATIONAL = "D_DISPUTATIONAL"
    E_HARMFUL = "E_HARMFUL"


class ModerationAction(str, Enum):
    """What was done, or is being done, about a message.

    Mirrors `contracts.ai.moderation.ModerationAction`. Note these are
    *actions*, not delivery states: `MessageStatus` (common.py) stays the
    chat-level lifecycle, and the mapping between them belongs to the
    gateway, not to either enum. See DECISIONS.md #6.

    **Delivery semantics**, spelled out here because leaving them to
    `docs/policy-taxonomy.md` alone already caused one wrong default
    (issue #65, where NUDGE was implemented as delivering):

    - `ALLOW` — delivered normally.
    - `NUDGE` — **not delivered to a circle.** The policy document is
      explicit: "Private, kindly-worded nudge to sender; not delivered to
      circles". The sender gets a private notice; the circle never sees
      the message. For a **1:1** the proposal calls this "a policy knob
      the organisation sets" (§7.3), so a NUDGE in a DM *does* deliver
      until the organisation decides otherwise — see
      `docs/policy-taxonomy-workshop.md` knob 1, default "circles only".
      So the behaviour is target-aware, not global.
    - `HOLD` — not delivered; waits for a human. Nothing is deleted.
    - `BLOCK` — not delivered; the sender is told and may appeal.

    Every non-`ALLOW` outcome owes the sender a notice (proposal §15,
    "never silent deletion") — see `ModerationEvent.notice_text` for what
    was said, and `MessageOut.moderation_notice` for how the sender
    actually receives it.
    """

    ALLOW = "ALLOW"
    NUDGE = "NUDGE"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class ModerationActorKind(str, Enum):
    """Who produced an event.

    CLASSIFIER is the automated check; MODERATOR is a human acting through
    the console. SYSTEM covers the cases that are neither — a hold that
    expired, a message swept by retention, an account erased on request —
    so that the trail can explain a state change nobody chose. Without it
    those rows would have to be attributed to a person who did not act.
    """

    CLASSIFIER = "classifier"
    MODERATOR = "moderator"
    SYSTEM = "system"


class ModerationEvent(BaseModel):
    """One row of the append-only trail. Never updated, never deleted.

    Not a `VersionedModel`: it is a value nested inside the payloads below,
    not a wire payload of its own — same rule as `MediaRef` (DECISIONS.md
    #9).

    `actor_id` is null exactly when `actor_kind` is CLASSIFIER or SYSTEM;
    the gateway enforces that, not this model, because "which user ids
    exist" is not a shape question. `confidence` is null for a human
    decision — a moderator does not have one, and reusing 0.0 or 1.0 would
    make "the human was certain" indistinguishable from "the model said
    so".
    """

    id: uuid.UUID
    message_id: uuid.UUID
    actor_kind: ModerationActorKind
    actor_id: uuid.UUID | None = None
    label: ModerationLabel
    action: ModerationAction
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    rationale: str = Field(
        description="English, for moderators and the audit record. Never shown to the sender."
    )
    note: str | None = Field(
        default=None,
        description="A human reviewer's own words, when they left any. Distinct from "
        "`rationale`, which for a classifier event is the model's reasoning.",
    )
    notice_text: str | None = Field(
        default=None,
        description="What the sender was actually told, in their own language. Recorded "
        "per event because the policy document's wording can change between a hold and "
        "the appeal that follows it, and the trail must show what the sender saw at the "
        "time — not what the current policy would say.",
    )
    policy_version: str = Field(
        description="`policy@<date>` from docs/policy-taxonomy.md's changelog, as stamped "
        "when this event was produced."
    )
    model_version: str | None = Field(default=None, description="Null for a human or system event.")
    degraded: bool = Field(
        default=False,
        description="True when the classifier failed closed (timeout, unparseable reply, "
        "backend error) and the HOLD is a fallback rather than a judgement. The console "
        "should show these differently: there is no model opinion to read.",
    )
    created_at: datetime


class ModerationQueueItem(BaseModel):
    """One message awaiting review, with everything the console needs to
    decide without a second round-trip.

    Both `original_text` and `pivot_text_en` are present because the
    proposal (§7.3) requires the moderator to see the original and the
    translation side by side: a moderator who reads only the English pivot
    is reviewing a translation of the thing the sender wrote, and devotional
    idiom is exactly where that goes wrong. `original_media_ref` is the
    voice note itself, so a moderator can listen when the transcript is the
    doubtful part.
    """

    message_id: uuid.UUID
    author_id: uuid.UUID
    author_display_name: str
    target_type: str = Field(description="`user` or `circle` — contracts.chat.common.TargetType.")
    target_id: uuid.UUID
    original_text: str | None = Field(
        default=None, description="As sent, in the sender's language. Null for a voice note."
    )
    original_language: str | None = None
    original_media_ref: MediaRef | None = None
    transcript: str | None = Field(
        default=None,
        min_length=1,
        description="A voice note's original-language text, as the ASR heard it. Same "
        "field name and meaning as MessageOut.transcript. Deliberately NOT folded into "
        "`original_text`: that field is what the sender *typed*, and a transcript is what "
        "the machine *heard* — which is exactly the thing that can be wrong. Collapsing "
        "them would hide ASR error at the moment a moderator most needs to see it, and "
        "the original recording stays the trust anchor when recognition errs (proposal "
        "§7.2). Null until the pipeline has transcribed it.",
    )
    transcript_language: str | None = Field(default=None, pattern=LANGUAGE_PATTERN)
    pivot_text_en: str | None = Field(
        default=None,
        description="The English pivot the classifier actually read. Null when the "
        "pipeline failed before producing one — in which case the moderator is reviewing "
        "the original alone, and should be shown that.",
    )
    latest_event: ModerationEvent = Field(
        description="The event that put this message in the queue. The full trail is a "
        "separate call; the queue carries only the current reason."
    )
    event_count: int = Field(
        ge=1,
        description="How many events this message already has — a second or third "
        "appearance is a different situation from a first hold, and the console should "
        "say so without fetching the trail.",
    )
    created_at: datetime = Field(description="When the message was sent, not when it was held.")


class ModerationQueueOut(VersionedModel):
    """Response body for `GET /moderation/queue`.

    Cursor pagination, not offset: the queue changes under the reader as
    moderators work it, and an offset would silently skip or repeat items
    when a row is released between pages.
    """

    items: list[ModerationQueueItem]
    next_cursor: str | None = Field(
        default=None, description="Opaque. Absent when this is the last page."
    )


class ModerationEventsOut(VersionedModel):
    """Response body for `GET /moderation/messages/{message_id}/events` —
    the full trail for one message, oldest first, for the console's audit
    view."""

    message_id: uuid.UUID
    events: list[ModerationEvent]


class ModerationReviewIn(VersionedModel):
    """Request body for `POST /moderation/messages/{message_id}/release`
    and `.../block`.

    The action is in the route, not this body: a console bug that posts the
    wrong body should not be able to turn a release into a block, and the
    two have different authorization and notification consequences.

    `expected_event_id` is the optimistic-concurrency guard. Two moderators
    can open the same queue item; without it, the second one's click
    silently overwrites a decision they never saw. The gateway should
    reject with a conflict when the message's latest event is no longer
    this one.
    """

    note: str | None = Field(
        default=None,
        max_length=2000,
        description="The reviewer's own words. Optional for a release, but worth asking "
        "for on a block — it is what the appeal is answered against.",
    )
    label: ModerationLabel | None = Field(
        default=None,
        description="Set when the moderator disagrees with the classifier's category and "
        "is correcting it. Null means 'the label stands, only the action changes'. A "
        "corrected label is the raw material for the exemplar list (proposal §7.3: "
        "prompt engineering, not training).",
    )
    expected_event_id: uuid.UUID | None = Field(
        default=None,
        description="The `latest_event.id` the console was showing. Omit only for a "
        "non-interactive caller that has no prior read.",
    )


class ModerationReviewOut(VersionedModel):
    """Response body for a release or block: the event just written, plus
    the message's resulting status, so the console can update a row without
    re-fetching the queue."""

    event: ModerationEvent
    message_status: str = Field(
        description="The message's new `contracts.chat.common.MessageStatus` value."
    )
    notice_sent: bool = Field(
        description="Whether the sender was actually notified. False means the action "
        "stands but the sender has not yet been told — a state the console must surface, "
        "because 'never silently' (proposal §15) is a promise about the sender's "
        "experience, not about the database."
    )
