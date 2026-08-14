from pydantic import Field

from contracts.ai.common import AudioRef, DegradedMode, StageTiming, VersionedModel
from contracts.ai.language import LanguageCode


class TranscribeRequest(VersionedModel):
    audio: AudioRef
    language_hint: LanguageCode | None = None


class TranscribeResponse(VersionedModel):
    text: str
    detected_language: LanguageCode
    model_version: str
    duration_ms: float
    stage_timings: list[StageTiming] = Field(default_factory=list)
    degraded: DegradedMode = Field(default_factory=DegradedMode.ok)
