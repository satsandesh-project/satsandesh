"""
Regenerates services/ai/tests/fixtures/*.json from the current contracts/ai/
models. Run this whenever a contract shape changes deliberately (and bump
CONTRACTS_VERSION); test_golden_fixtures.py then catches any further drift.

    python tools/generate_fixtures.py
"""

import json
from pathlib import Path

from contracts.ai.common import AudioFormat, AudioRef, DegradedMode, DegradedReason, StageTiming
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.language import LanguageCode
from contracts.ai.moderation import (
    ModerationAction,
    ModerationDecision,
    ModerationLabel,
    ModerationRequest,
)
from contracts.ai.pivot import PivotRequest, PivotResponse
from contracts.ai.render import RenderRequest, RenderResponse, RenderResult
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from pydantic import BaseModel

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _write(filename: str, instance: BaseModel) -> None:
    path = FIXTURES_DIR / filename
    path.write_text(
        json.dumps(instance.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {path.relative_to(FIXTURES_DIR.parents[1])}")


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    _write(
        "transcribe_request.json",
        TranscribeRequest(
            audio=AudioRef(
                uri="file:///tmp/sample.wav",
                format=AudioFormat.WAV_PCM16,
                duration_ms=30000,
                sample_rate_hz=16000,
            ),
            language_hint=LanguageCode.TELUGU,
        ),
    )
    _write(
        "transcribe_response.json",
        TranscribeResponse(
            text="నమస్తే, ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
            detected_language=LanguageCode.TELUGU,
            model_version="faster-whisper-small-int8@1",
            duration_ms=812.4,
            stage_timings=[
                StageTiming(stage="preprocess", duration_ms=40.1),
                StageTiming(stage="inference", duration_ms=700.3),
                StageTiming(stage="postprocess", duration_ms=72.0),
            ],
            degraded=DegradedMode.ok(),
        ),
    )

    _write(
        "pivot_request.json",
        PivotRequest(
            text="నమస్తే, ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
            source_language=LanguageCode.TELUGU,
        ),
    )
    _write(
        "pivot_response.json",
        PivotResponse(
            pivot_text="Namaste, what time is today's satsang?",
            source_language=LanguageCode.TELUGU,
            model_version="indictrans2-distilled@1",
            duration_ms=95.2,
            degraded=DegradedMode.ok(),
        ),
    )

    _write(
        "render_request.json",
        RenderRequest(
            pivot_text="Namaste, what time is today's satsang?",
            target_languages=[LanguageCode.HINDI, LanguageCode.TELUGU],
        ),
    )
    _write(
        "render_response.json",
        RenderResponse(
            results=[
                RenderResult(
                    language=LanguageCode.HINDI,
                    text="नमस्ते, आज सत्संग किस समय है?",
                    audio=AudioRef(
                        uri="s3://satsandesh-audio-dev/renders/abc123-hi.wav",
                        format=AudioFormat.WAV_PCM16,
                        duration_ms=2100,
                        sample_rate_hz=22050,
                    ),
                    model_version_translate="indictrans2-distilled@1",
                    model_version_tts="indic-tts-vits@1",
                    duration_ms=310.5,
                    degraded=DegradedMode.ok(),
                ),
                RenderResult(
                    language=LanguageCode.TELUGU,
                    text="నమస్తే, ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
                    audio=AudioRef(
                        uri="s3://satsandesh-audio-dev/renders/abc123-te.wav",
                        format=AudioFormat.WAV_PCM16,
                        duration_ms=2300,
                        sample_rate_hz=22050,
                    ),
                    model_version_translate="indictrans2-distilled@1",
                    model_version_tts="indic-tts-vits@1",
                    duration_ms=295.8,
                    degraded=DegradedMode(
                        active=True,
                        reason=DegradedReason.TTS_SKIPPED,
                        detail="VRAM headroom reserved for ASR queue",
                    ),
                ),
            ],
            degraded=DegradedMode(
                active=True,
                reason=DegradedReason.TTS_SKIPPED,
                detail="at least one target language degraded — see per-result detail",
            ),
        ),
    )

    _write(
        "moderation_request.json",
        ModerationRequest(text="Namaste, what time is today's satsang?"),
    )
    _write(
        "moderation_decision.json",
        ModerationDecision(
            label=ModerationLabel.D_DISPUTATIONAL,
            confidence=0.81,
            action=ModerationAction.NUDGE,
            rationale=(
                "Message reads as argumentative in tone; nudging sender toward "
                "gentler phrasing before it reaches the circle."
            ),
            nudge_text="దయచేసి మృదువుగా చెప్పండి.",
            nudge_language=LanguageCode.TELUGU,
            policy_version="policy@2026-08-13",
            model_version="qwen2.5-7b-instruct-q4@1",
            degraded=DegradedMode.ok(),
        ),
    )

    _write(
        "pipeline_error.json",
        PipelineError(
            code=ErrorCode.OUT_OF_MEMORY,
            message=(
                "Failed to allocate VRAM for Indic-TTS while ASR and moderation "
                "models were resident."
            ),
            stage="render.tts",
            detail={"requested_mb": 1800, "available_mb": 640},
        ),
    )


if __name__ == "__main__":
    main()
