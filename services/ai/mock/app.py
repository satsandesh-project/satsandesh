"""
Mock server for every services/ai/ endpoint. Other members build against this
from Week 2 while the real ASR/MT/TTS/moderation services are unfinished — the
canned responses always validate against the real Pydantic contracts, and
artificial per-request latency is configurable so downstream code can be tested
against realistic timing without a GPU.
"""

import asyncio
import os
import uuid

from contracts.ai.common import AudioFormat, AudioRef, DegradedMode, StageTiming
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
from fastapi import FastAPI, Header

app = FastAPI(title="SatSandesh AI — Mock Server", version="0.1.0")

DEFAULT_LATENCY_MS = float(os.environ.get("MOCK_LATENCY_MS", "50"))

_CANNED_TEXT: dict[LanguageCode, str] = {
    LanguageCode.ENGLISH: "Good morning, may your day be peaceful.",
    LanguageCode.HINDI: "सुप्रभात, आपका दिन शांतिपूर्ण हो।",
    LanguageCode.TELUGU: "శుభోదయం, మీ రోజు శాంతియుతంగా ఉండాలి.",
}

_MODERATION_KEYWORDS: dict[str, tuple[ModerationLabel, ModerationAction]] = {
    "urgent": (ModerationLabel.B_ORGANIZATIONAL, ModerationAction.ALLOW),
    "argue": (ModerationLabel.D_DISPUTATIONAL, ModerationAction.NUDGE),
    "hate": (ModerationLabel.E_HARMFUL, ModerationAction.BLOCK),
}


async def _sleep_for_latency(x_mock_latency_ms: str | None) -> float:
    """Per-request `X-Mock-Latency-Ms` header overrides the MOCK_LATENCY_MS env
    var default, which lets one client simulate a slow ASR call and a fast pivot
    call in the same test run."""
    latency_ms = float(x_mock_latency_ms) if x_mock_latency_ms is not None else DEFAULT_LATENCY_MS
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000)
    return latency_ms


@app.post("/v1/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: TranscribeRequest,
    x_mock_latency_ms: str | None = Header(default=None),
) -> TranscribeResponse:
    latency_ms = await _sleep_for_latency(x_mock_latency_ms)
    detected = request.language_hint or LanguageCode.HINDI
    return TranscribeResponse(
        text=_CANNED_TEXT[detected],
        detected_language=detected,
        model_version="faster-whisper-small-int8@mock",
        duration_ms=latency_ms,
        stage_timings=[
            StageTiming(stage="preprocess", duration_ms=round(latency_ms * 0.1, 2)),
            StageTiming(stage="inference", duration_ms=round(latency_ms * 0.8, 2)),
            StageTiming(stage="postprocess", duration_ms=round(latency_ms * 0.1, 2)),
        ],
        degraded=DegradedMode.ok(),
    )


@app.post("/v1/pivot", response_model=PivotResponse)
async def pivot(
    request: PivotRequest,
    x_mock_latency_ms: str | None = Header(default=None),
) -> PivotResponse:
    latency_ms = await _sleep_for_latency(x_mock_latency_ms)
    return PivotResponse(
        pivot_text=_CANNED_TEXT[LanguageCode.ENGLISH],
        source_language=request.source_language,
        model_version="indictrans2-distilled@mock",
        duration_ms=latency_ms,
        degraded=DegradedMode.ok(),
    )


@app.post("/v1/render", response_model=RenderResponse)
async def render(
    request: RenderRequest,
    x_mock_latency_ms: str | None = Header(default=None),
) -> RenderResponse:
    latency_ms = await _sleep_for_latency(x_mock_latency_ms)
    results = [
        RenderResult(
            language=lang,
            text=_CANNED_TEXT.get(lang, request.pivot_text),
            audio=AudioRef(
                uri=f"mock://audio/{uuid.uuid4()}.wav",
                format=AudioFormat.WAV_PCM16,
                duration_ms=1800,
                sample_rate_hz=22050,
            ),
            model_version_translate="indictrans2-distilled@mock",
            model_version_tts="indic-tts-vits@mock",
            duration_ms=latency_ms,
            degraded=DegradedMode.ok(),
        )
        for lang in request.target_languages
    ]
    return RenderResponse(results=results, degraded=DegradedMode.ok())


@app.post("/v1/moderate", response_model=ModerationDecision)
async def moderate(
    request: ModerationRequest,
    x_mock_latency_ms: str | None = Header(default=None),
) -> ModerationDecision:
    await _sleep_for_latency(x_mock_latency_ms)
    label, action = ModerationLabel.A_DEVOTIONAL, ModerationAction.ALLOW
    lowered = request.text.lower()
    for keyword, (kw_label, kw_action) in _MODERATION_KEYWORDS.items():
        if keyword in lowered:
            label, action = kw_label, kw_action
            break

    nudge_text: str | None = None
    nudge_language: LanguageCode | None = None
    if action is ModerationAction.NUDGE:
        nudge_text = "దయచేసి మృదువుగా చెప్పండి."
        nudge_language = LanguageCode.TELUGU

    return ModerationDecision(
        label=label,
        confidence=0.93,
        action=action,
        rationale=f"Mock classification based on keyword match for label {label.value}.",
        nudge_text=nudge_text,
        nudge_language=nudge_language,
        policy_version="mock-policy@0.1.0",
        model_version="qwen2.5-7b-instruct-q4@mock",
        degraded=DegradedMode.ok(),
    )
