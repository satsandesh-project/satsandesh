"""
Mock server for every services/ai/ endpoint. Other members build against this
from Week 2 while the real ASR/MT/TTS/moderation services are unfinished — the
canned responses always validate against the real Pydantic contracts, and
artificial per-request latency is configurable so downstream code can be tested
against realistic timing without a GPU.

Two opt-in headers let M2 exercise error/degraded-mode handling before the
Week 8 real-service cutover, without changing default (happy-path) behavior:
`X-Mock-Error` (any endpoint) and `X-Mock-Degraded` (/v1/render). See
services/ai/mock/README.md for examples.
"""

import asyncio
import os
import uuid

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
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse

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


def _maybe_inject_error(x_mock_error: str | None, stage: str) -> JSONResponse | None:
    """Opt-in `X-Mock-Error: <ErrorCode>` header. Returns a JSONResponse to send
    instead of the endpoint's normal canned response, or None on the happy path
    (the default — no caller sees a behavior change unless they set the header).
    An unrecognized value is a 400 telling the caller why, never a silent
    fallback to success."""
    if x_mock_error is None:
        return None
    try:
        code = ErrorCode(x_mock_error)
    except ValueError:
        valid_values = [c.value for c in ErrorCode]
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_mock_error_code",
                "message": (
                    f"X-Mock-Error value {x_mock_error!r} is not a valid ErrorCode. "
                    f"Valid values: {', '.join(valid_values)}"
                ),
                "valid_values": valid_values,
            },
        )
    error = PipelineError(
        code=code,
        message=f"mock-injected error via X-Mock-Error header (code={code.value})",
        stage=stage,
        detail={"injected": True},
    )
    return JSONResponse(status_code=422, content=error.model_dump(mode="json"))


@app.get("/health/live")
async def health_live() -> dict:
    """200 the instant the process is up. Matches services/ai/speech/app.py's
    shape exactly so M2's health-check logic keeps working unchanged when
    pointed at the real service in Week 8."""
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready() -> dict:
    """Always ready/200 immediately — the mock has no model to warm up. Field
    names match services/ai/speech/app.py's ready shape; load/warmup durations
    are 0 since there's nothing to load or warm up."""
    return {
        "status": "ready",
        "model_version": "mock@0.1.0",
        "load_duration_ms": 0.0,
        "warmup_duration_ms": 0.0,
    }


@app.post("/v1/transcribe", response_model=TranscribeResponse)
async def transcribe(
    request: TranscribeRequest,
    x_mock_latency_ms: str | None = Header(default=None),
    x_mock_error: str | None = Header(default=None),
) -> TranscribeResponse | JSONResponse:
    error_response = _maybe_inject_error(x_mock_error, stage="transcribe.mock_injected")
    if error_response is not None:
        return error_response
    latency_ms = await _sleep_for_latency(x_mock_latency_ms)
    detected = request.language_hint or LanguageCode.HINDI
    return TranscribeResponse(
        text=_CANNED_TEXT[detected],
        detected_language=detected,
        model_version="faster-whisper-small-int8@mock",
        duration_ms=latency_ms,
        stage_timings=[
            # Real Phase 2 measurements: inference dominates almost completely,
            # decode and postprocess are both near-zero. 1/98/1, not 10/80/10.
            StageTiming(stage="preprocess", duration_ms=round(latency_ms * 0.01, 2)),
            StageTiming(stage="inference", duration_ms=round(latency_ms * 0.98, 2)),
            StageTiming(stage="postprocess", duration_ms=round(latency_ms * 0.01, 2)),
        ],
        degraded=DegradedMode.ok(),
    )


@app.post("/v1/pivot", response_model=PivotResponse)
async def pivot(
    request: PivotRequest,
    x_mock_latency_ms: str | None = Header(default=None),
    x_mock_error: str | None = Header(default=None),
) -> PivotResponse | JSONResponse:
    error_response = _maybe_inject_error(x_mock_error, stage="pivot.mock_injected")
    if error_response is not None:
        return error_response
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
    x_mock_error: str | None = Header(default=None),
    x_mock_degraded: str | None = Header(default=None),
) -> RenderResponse | JSONResponse:
    error_response = _maybe_inject_error(x_mock_error, stage="render.mock_injected")
    if error_response is not None:
        return error_response
    latency_ms = await _sleep_for_latency(x_mock_latency_ms)
    inject_degraded = x_mock_degraded is not None and x_mock_degraded.strip().lower() == "true"
    results = []
    for index, lang in enumerate(request.target_languages):
        degraded = DegradedMode.ok()
        if inject_degraded and index == 0:
            degraded = DegradedMode(
                active=True,
                reason=DegradedReason.TTS_SKIPPED,
                detail=(
                    "mock-injected via X-Mock-Degraded header: TTS skipped to "
                    "simulate VRAM pressure shedding load"
                ),
            )
        results.append(
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
                degraded=degraded,
            )
        )
    return RenderResponse(results=results, degraded=DegradedMode.ok())


@app.post("/v1/moderate", response_model=ModerationDecision)
async def moderate(
    request: ModerationRequest,
    x_mock_latency_ms: str | None = Header(default=None),
    x_mock_error: str | None = Header(default=None),
) -> ModerationDecision | JSONResponse:
    error_response = _maybe_inject_error(x_mock_error, stage="moderate.mock_injected")
    if error_response is not None:
        return error_response
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
