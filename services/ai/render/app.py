"""
Real render service -- English pivot text -> per-target-language text -> per-target-
language audio, served as POST /v1/render against contracts/ai/render.py.

Translate (IndicTrans2 en-indic) and synthesize (Piper) are two internal steps that
get merged into one RenderResult per target language. Sibling to services/ai/mt/
(which does the reverse, source -> English, direction).
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from contracts.ai.common import (
    AudioFormat,
    AudioRef,
    DegradedMode,
    DegradedReason,
)
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.language import LanguageCode
from contracts.ai.render import RenderRequest, RenderResponse, RenderResult
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from services.ai.render.engine import MtEngine, TtsEngine, UnsupportedTargetLanguageError
from services.ai.render.settings import Settings

logger = logging.getLogger("services.ai.render.app")

settings = Settings.from_env()
mt_engine = MtEngine(
    model_name=settings.mt_model_name,
    device=settings.mt_device,
    num_beams=settings.num_beams,
    max_length=settings.max_length,
    hf_token=settings.hf_token,
)
tts_engine = TtsEngine(
    voices={
        LanguageCode.TELUGU: settings.voice_te,
        LanguageCode.HINDI: settings.voice_hi,
    },
    voice_dir=settings.voice_dir,
)

_WARMUP_TEXT_EN = "Hello"
_WARMUP_TTS_TEXT = {
    LanguageCode.TELUGU: "నమస్కారం",
    LanguageCode.HINDI: "नमस्ते",
}

_NO_VOICE_MODEL_VERSION = "none (no TTS voice for this language)"
_PASSTHROUGH_MODEL_VERSION = "passthrough (no MT model invoked; target_language == en)"

_ready = False


def _pipeline_error(
    code: ErrorCode, message: str, stage: str, status_code: int, detail: dict | None = None
) -> JSONResponse:
    error = PipelineError(code=code, message=message, stage=stage, detail=detail)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


async def _startup() -> None:
    global _ready
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    mt_engine.load()
    mt_engine.warm_up(_WARMUP_TEXT_EN, LanguageCode.HINDI)
    tts_engine.load()
    tts_engine.warm_up(_WARMUP_TTS_TEXT, settings.output_dir)
    _ready = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(
    title="SatSandesh AI — Render (English -> target text + audio)",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/live")
async def health_live() -> dict:
    """200 the instant the process is up. Touches nothing else."""
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """503 until the MT model and every Piper voice are loaded AND warm."""
    if not _ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "mt_model_version": mt_engine.model_version,
            "mt_load_duration_ms": mt_engine.load_duration_ms,
            "mt_warmup_duration_ms": mt_engine.warmup_duration_ms,
            "tts_voices": {
                lang.value: tts_engine.model_version(lang)
                for lang in sorted(tts_engine.languages, key=lambda c: c.value)
            },
            "tts_load_duration_ms": tts_engine.load_durations_ms,
            "tts_warmup_duration_ms": tts_engine.warmup_durations_ms,
        },
    )


def _no_audio() -> AudioRef:
    # RenderResult.audio is required, not Optional, so a text-only result has to carry
    # *something*. An empty uri + zero duration is the explicit "no audio" marker; callers
    # must check degraded.reason (TEXT_ONLY / TTS_SKIPPED) before dereferencing it.
    return AudioRef(uri="", format=AudioFormat.WAV_PCM16, duration_ms=0)


def _render_one(pivot_text: str, language: LanguageCode) -> RenderResult:
    start = time.perf_counter()

    # No TTS voice for this language (e.g. en -- the spike verified only hi/te voices).
    # Still translate/pass through the text, mark the result degraded, don't fail the
    # whole fan-out. This is the per-language `degraded` the contract was built for.
    if language not in tts_engine.languages:
        text = pivot_text
        translate_version = _PASSTHROUGH_MODEL_VERSION
        if language != LanguageCode.ENGLISH:
            text = mt_engine.translate(pivot_text, language).text
            translate_version = mt_engine.model_version
        return RenderResult(
            language=language,
            text=text,
            audio=_no_audio(),
            model_version_translate=translate_version,
            model_version_tts=_NO_VOICE_MODEL_VERSION,
            duration_ms=(time.perf_counter() - start) * 1000,
            degraded=DegradedMode(
                active=True,
                reason=DegradedReason.TEXT_ONLY,
                detail=f"no TTS voice available for {language.value!r}",
            ),
        )

    translation = mt_engine.translate(pivot_text, language)

    out_path = settings.output_dir / f"render-{uuid.uuid4().hex}-{language.value}.wav"
    try:
        synth = tts_engine.synthesize(translation.text, language, out_path)
    except UnsupportedTargetLanguageError:
        raise
    except Exception as exc:
        logger.exception("TTS failed for %s", language.value)
        return RenderResult(
            language=language,
            text=translation.text,
            audio=_no_audio(),
            model_version_translate=mt_engine.model_version,
            model_version_tts=tts_engine.model_version(language),
            duration_ms=(time.perf_counter() - start) * 1000,
            degraded=DegradedMode(
                active=True,
                reason=DegradedReason.TTS_SKIPPED,
                detail=f"TTS failed: {type(exc).__name__}",
            ),
        )

    logger.info(
        "render %s timings (ms): translate=%.1f synthesize=%.1f (audio %d ms)",
        language.value,
        translation.duration_ms,
        synth.duration_ms,
        synth.audio_duration_ms,
    )
    return RenderResult(
        language=language,
        text=translation.text,
        audio=AudioRef(
            uri=synth.path.resolve().as_uri(),
            format=AudioFormat.WAV_PCM16,
            duration_ms=synth.audio_duration_ms,
            sample_rate_hz=synth.sample_rate_hz,
        ),
        model_version_translate=mt_engine.model_version,
        model_version_tts=tts_engine.model_version(language),
        duration_ms=translation.duration_ms + synth.duration_ms,
    )


@app.post("/v1/render", response_model=None)
async def render(request: RenderRequest) -> RenderResponse | JSONResponse:
    if not _ready:
        return _pipeline_error(
            ErrorCode.MODEL_LOAD_FAILED,
            "Render models are not ready yet (still loading or warm-up incomplete).",
            stage="render.startup",
            status_code=503,
        )

    results: list[RenderResult] = []
    for language in request.target_languages:
        # Empty text: nothing to translate or say. Same empty-passthrough choice as
        # services/ai/mt/app.py -- a per-language text-only result, not an error.
        if not request.pivot_text.strip():
            results.append(
                RenderResult(
                    language=language,
                    text="",
                    audio=_no_audio(),
                    model_version_translate=_PASSTHROUGH_MODEL_VERSION,
                    model_version_tts=_NO_VOICE_MODEL_VERSION,
                    duration_ms=0.0,
                    degraded=DegradedMode(
                        active=True,
                        reason=DegradedReason.TEXT_ONLY,
                        detail="empty pivot_text; nothing to translate or synthesize",
                    ),
                )
            )
            continue
        try:
            results.append(_render_one(request.pivot_text, language))
        except UnsupportedTargetLanguageError as exc:
            return _pipeline_error(
                ErrorCode.UNSUPPORTED_LANGUAGE,
                str(exc),
                stage="render.translate",
                status_code=422,
                detail={"target_language": language.value},
            )

    degraded_langs = [r.language.value for r in results if r.degraded.active]
    top_degraded = (
        DegradedMode(
            active=True,
            reason=next(r.degraded.reason for r in results if r.degraded.active),
            detail=f"degraded languages: {degraded_langs}",
        )
        if degraded_langs
        else DegradedMode.ok()
    )
    return RenderResponse(results=results, degraded=top_degraded)
