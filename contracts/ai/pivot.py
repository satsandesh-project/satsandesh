from pydantic import Field

from contracts.ai.common import DegradedMode, VersionedModel
from contracts.ai.language import LanguageCode


class PivotRequest(VersionedModel):
    text: str
    source_language: LanguageCode


class PivotResponse(VersionedModel):
    pivot_text: str
    source_language: LanguageCode
    model_version: str
    duration_ms: float
    degraded: DegradedMode = Field(default_factory=DegradedMode.ok)
