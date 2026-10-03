"""
What the pipeline made of one message for one language: the receiver-facing
half of the orchestrator's output (transcribe -> pivot -> moderate ->
render). A `MessageOut` carries zero or more of these in `renderings`.

Why embedded in `MessageOut` rather than behind a second endpoint. The
pipeline runs before a message is delivered (a message is held or blocked,
not sent, if moderation says so), so by the time a receiver can see a
message its renderings already exist. Embedding them means one frame / one
sync page carries everything the receiver screen needs -- no per-message
follow-up request on the flaky networks this app targets, and the offline
sync path gets them for free. See DECISIONS.md #16.

What is NOT in here, deliberately:
  - the original text or audio -- those stay on `MessageOut.text` /
    `MessageOut.media_ref`, so "original one tap away" needs no extra field;
  - the English pivot -- it is a moderation input, not something a receiver
    reads (a moderator sees it via `ModerationQueueItem.pivot_text_en`);
  - model versions -- an audit/eval concern the gateway stores but a
    receiver has no use for.

A rendering exists only for a language the message was actually rendered
into, and (by convention, not enforced here -- this model can't see the
message) not for the message's own source language: a receiver whose
language matches the original simply uses the original.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from contracts.chat.common import MediaRef

MAX_RENDERINGS_PER_MESSAGE = 8
"""
Upper bound on `MessageOut.renderings` (docs/security-checklist.md, Part A:
anything that fans out must have one). v1 supports three languages; eight
leaves room for the stretch languages without letting a bug or a hostile
producer put an unbounded list on every sync page.
"""


class RenderingDegradedReason(str, Enum):
    """Why a rendering is less than complete. Values mirror
    `contracts.ai.common.DegradedReason` minus its `none` (absence of a
    reason is `degraded_reason is None` here), and are duplicated rather than
    imported for the same reason as `AudioFormat` -- see DECISIONS.md #5. The
    drift guard is a test comparing the two value sets."""

    TEXT_ONLY = "text_only"
    TTS_SKIPPED = "tts_skipped"
    MODEL_FALLBACK = "model_fallback"
    RATE_LIMITED = "rate_limited"


# Reasons that say "there is no audio". A rendering claiming one of these and
# also carrying audio is self-contradictory, and a client would have to guess
# which half to believe.
_NO_AUDIO_REASONS = frozenset(
    {RenderingDegradedReason.TEXT_ONLY, RenderingDegradedReason.TTS_SKIPPED}
)


class Rendering(BaseModel):
    """One language's version of a message. Not a `VersionedModel`: it is a
    value nested inside `MessageOut`, not a wire payload of its own -- same
    rule as `MediaRef` and `ModerationEvent` (DECISIONS.md #9).

    `language` is a bare lowercase BCP-47 primary subtag (`hi`, `te`, `en`),
    the same shape `contracts.ai.language.LanguageCode` uses on the wire,
    kept a validated string rather than that enum so this package does not
    import `contracts/ai/` (DECISIONS.md #5). A client compares it with the
    user's `preferred_language`, so a regional form like `hi-IN` would
    silently never match -- hence rejected, not tolerated.

    `text` is never empty: the pipeline reports `""` for a silent note, and
    the orchestrator must omit that rendering rather than hand a client a
    blank bubble.

    `audio` is `None` when no speech was produced; `degraded_reason` says
    why when that is a shortfall rather than a choice. `None` reason means
    the rendering is complete.
    """

    language: str = Field(pattern=r"^[a-z]{2,3}$")
    text: str = Field(min_length=1)
    audio: MediaRef | None = None
    degraded_reason: RenderingDegradedReason | None = None

    @model_validator(mode="after")
    def _no_audio_reasons_carry_no_audio(self) -> Rendering:
        if self.degraded_reason in _NO_AUDIO_REASONS and self.audio is not None:
            raise ValueError(
                f"degraded_reason {self.degraded_reason.value!r} means no audio, but audio is set"
            )
        return self
