import pytest
from contracts.ai.common import (
    CONTRACTS_VERSION,
    AudioFormat,
    AudioRef,
    DegradedMode,
    DegradedReason,
    StageTiming,
)
from pydantic import ValidationError


def test_degraded_mode_defaults_to_not_active() -> None:
    d = DegradedMode()
    assert d.active is False
    assert d.reason is DegradedReason.NONE


def test_degraded_mode_ok_helper() -> None:
    d = DegradedMode.ok()
    assert d.active is False
    assert d.reason is DegradedReason.NONE


def test_audio_ref_requires_known_format() -> None:
    with pytest.raises(ValidationError):
        AudioRef.model_validate({"uri": "file:///tmp/a.wav", "format": "flac"})


def test_audio_ref_minimal_construction() -> None:
    ref = AudioRef(uri="file:///tmp/a.wav", format=AudioFormat.WAV_PCM16)
    assert ref.duration_ms is None
    assert ref.sample_rate_hz is None


def test_stage_timing_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError):
        StageTiming(stage="inference", duration_ms=-1)


def test_contracts_version_is_a_dotted_string() -> None:
    assert isinstance(CONTRACTS_VERSION, str)
    assert CONTRACTS_VERSION.count(".") == 2
