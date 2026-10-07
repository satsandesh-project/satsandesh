from enum import Enum

from pydantic import BaseModel, Field

CONTRACTS_VERSION = "0.2.0"
"""
Schema-shape version for everything in contracts/ai/. Bump this when any field is
added, renamed, retyped, or removed, so a downstream consumer can tell which shape
it is looking at. Distinct from a model's own `model_version` (which model weights
produced a value) and moderation's `policy_version` (which prompt/policy produced
a decision) — those change independently and more often than the wire shape does.

0.1.0 -> 0.2.0: AudioFormat gained `webm_opus` (additive; every 0.1.0 payload still
parses). It is what a browser MediaRecorder produces, so the gateway no longer has
to label WebM as `ogg_opus`.
"""


class VersionedModel(BaseModel):
    """Base for every AI payload model; stamps the contract shape version."""

    contract_version: str = CONTRACTS_VERSION


class AudioFormat(str, Enum):
    WAV_PCM16 = "wav_pcm16"
    OGG_OPUS = "ogg_opus"
    WEBM_OPUS = "webm_opus"
    MP3 = "mp3"


class AudioRef(BaseModel):
    """
    A reference to audio, not embedded audio bytes. Week 1 has no gateway and no
    blob storage yet, so `uri` is deliberately unconstrained beyond being a string
    (a local file path today, likely an object-storage URI later) — see Open
    Questions for who owns that storage layer.
    """

    uri: str
    format: AudioFormat
    duration_ms: int | None = None
    sample_rate_hz: int | None = None


class StageTiming(BaseModel):
    stage: str
    duration_ms: float = Field(ge=0)


class DegradedReason(str, Enum):
    NONE = "none"
    TEXT_ONLY = "text_only"
    TTS_SKIPPED = "tts_skipped"
    MODEL_FALLBACK = "model_fallback"
    RATE_LIMITED = "rate_limited"


class DegradedMode(BaseModel):
    """
    Carried on every response so a caller can tell when the GPU shed load (e.g.
    TTS skipped to protect VRAM headroom for ASR) instead of silently returning a
    partial result that looks complete.
    """

    active: bool = False
    reason: DegradedReason = DegradedReason.NONE
    detail: str | None = None

    @classmethod
    def ok(cls) -> "DegradedMode":
        return cls(active=False, reason=DegradedReason.NONE)
