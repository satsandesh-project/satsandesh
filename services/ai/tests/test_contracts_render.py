import pytest
from contracts.ai.common import AudioFormat, AudioRef, DegradedMode, DegradedReason
from contracts.ai.language import LanguageCode
from contracts.ai.render import RenderRequest, RenderResponse, RenderResult
from pydantic import ValidationError


def _audio_ref(name: str) -> AudioRef:
    return AudioRef(uri=f"file:///tmp/{name}.wav", format=AudioFormat.WAV_PCM16)


def test_render_request_fan_out_multiple_languages() -> None:
    req = RenderRequest(
        pivot_text="Good morning",
        target_languages=[LanguageCode.HINDI, LanguageCode.TELUGU],
    )
    assert req.target_languages == [LanguageCode.HINDI, LanguageCode.TELUGU]


def test_render_request_dedupes_preserving_first_occurrence_order() -> None:
    req = RenderRequest(
        pivot_text="Good morning",
        target_languages=[
            LanguageCode.TELUGU,
            LanguageCode.HINDI,
            LanguageCode.TELUGU,
            LanguageCode.ENGLISH,
            LanguageCode.HINDI,
        ],
    )
    assert req.target_languages == [
        LanguageCode.TELUGU,
        LanguageCode.HINDI,
        LanguageCode.ENGLISH,
    ]


def test_render_request_rejects_empty_target_languages() -> None:
    with pytest.raises(ValidationError):
        RenderRequest(pivot_text="Good morning", target_languages=[])


def test_render_response_holds_one_result_per_target_language() -> None:
    resp = RenderResponse(
        results=[
            RenderResult(
                language=LanguageCode.HINDI,
                text="Good morning",
                audio=_audio_ref("hi"),
                model_version_translate="indictrans2-distilled@1",
                model_version_tts="indic-tts-vits@1",
                duration_ms=42.0,
            )
        ]
    )
    assert len(resp.results) == 1
    assert resp.degraded.active is False


def test_render_result_can_independently_degrade_per_language() -> None:
    """One target language's TTS can be skipped under VRAM pressure while others
    in the same fan-out succeed — degraded status must be expressible per-result,
    not just at the response top level."""
    result = RenderResult(
        language=LanguageCode.TELUGU,
        text="Good morning",
        audio=_audio_ref("te"),
        model_version_translate="indictrans2-distilled@1",
        model_version_tts="indic-tts-vits@1",
        duration_ms=42.0,
        degraded=DegradedMode(
            active=True, reason=DegradedReason.TTS_SKIPPED, detail="VRAM pressure"
        ),
    )
    assert result.degraded.active is True
    assert result.degraded.reason is DegradedReason.TTS_SKIPPED
