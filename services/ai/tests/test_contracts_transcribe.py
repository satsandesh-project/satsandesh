import pytest
from contracts.ai.common import AudioFormat, AudioRef, StageTiming
from contracts.ai.language import LanguageCode
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from pydantic import ValidationError


def _audio_ref() -> AudioRef:
    return AudioRef(uri="file:///tmp/sample.wav", format=AudioFormat.WAV_PCM16)


def test_transcribe_request_minimal() -> None:
    req = TranscribeRequest(audio=_audio_ref())
    assert req.language_hint is None


def test_transcribe_request_with_language_hint() -> None:
    req = TranscribeRequest(audio=_audio_ref(), language_hint=LanguageCode.TELUGU)
    assert req.language_hint is LanguageCode.TELUGU


def test_transcribe_response_requires_model_version() -> None:
    with pytest.raises(ValidationError):
        TranscribeResponse.model_validate(
            {
                "text": "namaste",
                "detected_language": "hi",
                "duration_ms": 120.0,
            }
        )


def test_transcribe_response_full_construction() -> None:
    resp = TranscribeResponse(
        text="namaste",
        detected_language=LanguageCode.HINDI,
        model_version="faster-whisper-small-int8@1",
        duration_ms=120.0,
        stage_timings=[StageTiming(stage="inference", duration_ms=100.0)],
    )
    assert resp.degraded.active is False
    assert resp.contract_version
