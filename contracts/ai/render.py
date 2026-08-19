from pydantic import BaseModel, Field, field_validator

from contracts.ai.common import AudioRef, DegradedMode, VersionedModel
from contracts.ai.language import LanguageCode


class RenderRequest(VersionedModel):
    """A circle message fans out to many receivers, so one request can target
    multiple languages at once."""

    pivot_text: str
    target_languages: list[LanguageCode]

    @field_validator("target_languages")
    @classmethod
    def _require_and_dedupe(cls, value: list[LanguageCode]) -> list[LanguageCode]:
        if not value:
            raise ValueError("target_languages must not be empty")
        seen: set[LanguageCode] = set()
        deduped: list[LanguageCode] = []
        for lang in value:
            if lang not in seen:
                seen.add(lang)
                deduped.append(lang)
        return deduped


class RenderResult(BaseModel):
    """One target language's result. Carries its own `degraded` because a
    fan-out can partially degrade — e.g. TTS skipped for one language under VRAM
    pressure while translation and TTS both succeed for the others."""

    language: LanguageCode
    text: str
    audio: AudioRef
    model_version_translate: str
    model_version_tts: str
    duration_ms: float
    degraded: DegradedMode = Field(default_factory=DegradedMode.ok)


class RenderResponse(VersionedModel):
    results: list[RenderResult]
    degraded: DegradedMode = Field(default_factory=DegradedMode.ok)
