from enum import Enum

from pydantic import Field

from contracts.ai.common import DegradedMode, VersionedModel
from contracts.ai.language import LanguageCode


class ModerationLabel(str, Enum):
    A_DEVOTIONAL = "A_DEVOTIONAL"
    B_ORGANIZATIONAL = "B_ORGANIZATIONAL"
    C_PERSONAL = "C_PERSONAL"
    D_DISPUTATIONAL = "D_DISPUTATIONAL"
    E_HARMFUL = "E_HARMFUL"


class ModerationAction(str, Enum):
    ALLOW = "ALLOW"
    NUDGE = "NUDGE"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


class ModerationRequest(VersionedModel):
    text: str


class ModerationDecision(VersionedModel):
    label: ModerationLabel
    confidence: float = Field(ge=0.0, le=1.0)
    action: ModerationAction = Field(
        description="Independent of label/confidence: a low-confidence call can "
        "always be routed to HOLD regardless of which label the classifier guessed."
    )
    rationale: str = Field(description="English, for moderators/ops — not shown to the sender.")
    nudge_text: str | None = Field(
        default=None,
        description="Sender-facing message, set when action is NUDGE (or HOLD, if "
        "shown while awaiting review). Must be in the sender's own language, not "
        "English — see nudge_language.",
    )
    nudge_language: LanguageCode | None = None
    policy_version: str
    model_version: str
    degraded: DegradedMode = Field(default_factory=DegradedMode.ok)
