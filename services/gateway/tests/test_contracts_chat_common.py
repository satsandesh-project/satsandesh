import pytest
from contracts.chat.common import AudioFormat, MediaRef, MessageKind, MessageStatus, TargetType
from pydantic import ValidationError


def test_target_type_is_a_closed_enum() -> None:
    assert TargetType.USER.value == "user"
    assert TargetType.CIRCLE.value == "circle"


def test_message_kind_is_a_closed_enum() -> None:
    assert {k.value for k in MessageKind} == {"text", "voice"}


def test_message_status_is_a_closed_enum() -> None:
    assert {s.value for s in MessageStatus} == {
        "pending",
        "delivered",
        "held",
        "blocked",
        "failed",
        "sent",
        "cancelled",
    }


def test_audio_format_is_a_closed_enum() -> None:
    assert {f.value for f in AudioFormat} == {"webm_opus", "ogg_opus", "wav_pcm16", "mp3"}


def test_media_ref_requires_uri_and_format() -> None:
    with pytest.raises(ValidationError):
        MediaRef.model_validate({})
    with pytest.raises(ValidationError):
        # uri alone isn't enough any more -- format is now required too,
        # see DECISIONS.md #14.
        MediaRef.model_validate({"uri": "mock://audio/a.wav"})
    ref = MediaRef(uri="mock://audio/a.wav", format=AudioFormat.WAV_PCM16, duration_ms=1200)
    assert ref.duration_ms == 1200


def test_media_ref_rejects_a_uri_with_no_scheme() -> None:
    # A bare filename or path can't say where a stored artifact lives --
    # see DECISIONS.md #13.
    with pytest.raises(ValidationError):
        MediaRef(uri="just-a-filename.wav", format=AudioFormat.WAV_PCM16)
    with pytest.raises(ValidationError):
        MediaRef(uri="", format=AudioFormat.WAV_PCM16)


def test_media_ref_accepts_any_well_formed_scheme() -> None:
    # Deliberately not constrained to one scheme -- see DECISIONS.md #13.
    # "media:" is this package's own indirection scheme (contracts/chat/media.py),
    # but the field itself only requires a real URI shape, not that one
    # specific scheme.
    for uri in ("media:abc123", "mock://audio/a.wav", "s3://bucket/key", "file:///tmp/x.wav"):
        assert MediaRef(uri=uri, format=AudioFormat.OGG_OPUS).uri == uri


def test_media_ref_rejects_a_negative_duration() -> None:
    with pytest.raises(ValidationError):
        MediaRef(uri="media:abc123", format=AudioFormat.MP3, duration_ms=-1)
