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


def test_audio_ref_webm_opus_round_trips() -> None:
    ref = AudioRef(uri="file:///tmp/a.webm", format=AudioFormat.WEBM_OPUS, duration_ms=2000)
    assert ref.model_dump(mode="json")["format"] == "webm_opus"
    assert AudioRef.model_validate_json(ref.model_dump_json()) == ref
    assert AudioRef.model_validate({"uri": "file:///tmp/a.webm", "format": "webm_opus"}).format is (
        AudioFormat.WEBM_OPUS
    )


def test_audio_format_values_are_the_agreed_set() -> None:
    assert {f.value for f in AudioFormat} == {"wav_pcm16", "ogg_opus", "webm_opus", "mp3"}


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
