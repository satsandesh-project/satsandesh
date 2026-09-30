"""
IndicConformer ASR service -- a drop-in alternative to services/ai/speech/ (faster-whisper),
implementing the same contracts/ai/transcribe.py contract on POST /v1/transcribe.

The model (gated on HF, needs HF_TOKEN) is loaded once during startup, not per request.
Unlike faster-whisper this model cannot auto-detect language: `language_hint` is required
and must be hi or te, otherwise the request gets a PipelineError (UNSUPPORTED_LANGUAGE).
`detected_language` in the response is the hint echoed back, not a detection.

Run:  PYTHONPATH=. uvicorn services.ai.speech_indicconformer.app:app --port 8004
"""

from __future__ import annotations

import logging
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from contracts.ai.common import AudioFormat, DegradedMode, StageTiming
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.language import LanguageCode
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from services.ai.speech_indicconformer.engine import (
    FfmpegDecodeError,
    FfmpegNotFoundError,
    IndicConformerEngine,
    UnsupportedLanguageError,
    UnsupportedWavError,
    decode_via_ffmpeg,
    decode_wav_pcm16,
)
from services.ai.speech_indicconformer.settings import Settings

logger = logging.getLogger("services.ai.speech_indicconformer.app")

_WARMUP_AUDIO_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tone_2s.wav"

# Built during startup (Settings.from_env() fails fast there if HF_TOKEN is missing),
# not at import, so unit tests can import this module and inject a stub engine.
engine: Any = None
_ready = False


def _resolve_local_path(uri: str) -> Path:
    """AudioRef.uri -> local path (file:// incl. Windows file:///C:/..., or a plain path).

    Duplicated from services/ai/speech/app.py on purpose: importing it from there would
    execute that whole app module (its Settings + FastAPI app) for ten lines. A candidate
    for a shared helper module if the two services stay side by side.
    """
    if uri.startswith("file://"):
        raw_path = unquote(urlparse(uri).path)
        if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path.lstrip("/")
        return Path(raw_path)
    return Path(uri)


def _pipeline_error(
    code: ErrorCode, message: str, stage: str, status_code: int, detail: dict | None = None
) -> JSONResponse:
    error = PipelineError(code=code, message=message, stage=stage, detail=detail)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


async def _startup() -> None:
    global engine, _ready
    settings = Settings.from_env()
    engine = IndicConformerEngine(
        model_name=settings.model_name,
        hf_token=settings.hf_token,
        decode_mode=settings.decode_mode,
    )
    engine.load()
    engine.warm_up(decode_wav_pcm16(_WARMUP_AUDIO_PATH))
    _ready = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(
    title="SatSandesh AI — Speech (IndicConformer ASR)",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/live")
async def health_live() -> dict:
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    if not _ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "model_version": engine.model_version,
            "decode_mode": engine.decode_mode,
            "load_duration_ms": engine.load_duration_ms,
            "warmup_duration_ms": engine.warmup_duration_ms,
        },
    )


@app.post("/v1/transcribe", response_model=None)
async def transcribe(request: TranscribeRequest) -> TranscribeResponse | JSONResponse:
    if not _ready:
        return _pipeline_error(
            ErrorCode.MODEL_LOAD_FAILED,
            "ASR model is not ready yet (still loading or warm-up incomplete).",
            stage="transcribe.startup",
            status_code=503,
        )

    path = _resolve_local_path(request.audio.uri)
    decode_fn = (
        decode_wav_pcm16 if request.audio.format == AudioFormat.WAV_PCM16 else decode_via_ffmpeg
    )

    decode_start = time.perf_counter()
    try:
        audio = decode_fn(path)
    except (FileNotFoundError, UnsupportedWavError, wave.Error) as exc:
        return _pipeline_error(
            ErrorCode.AUDIO_FETCH_FAILED,
            f"could not read/decode audio at {request.audio.uri!r}: {exc}",
            stage="transcribe.decode",
            status_code=422,
            detail={"uri": request.audio.uri},
        )
    except FfmpegNotFoundError as exc:
        return _pipeline_error(
            ErrorCode.AUDIO_FETCH_FAILED,
            f"ffmpeg is required to decode {request.audio.format.value!r} audio "
            f"but was not found: {exc}",
            stage="transcribe.decode",
            status_code=422,
            detail={"format": request.audio.format.value},
        )
    except FfmpegDecodeError as exc:
        return _pipeline_error(
            ErrorCode.AUDIO_FETCH_FAILED,
            f"ffmpeg could not decode audio at {request.audio.uri!r}: {exc}",
            stage="transcribe.decode",
            status_code=422,
            detail={"uri": request.audio.uri, "format": request.audio.format.value},
        )
    decode_duration_ms = (time.perf_counter() - decode_start) * 1000

    language_hint = request.language_hint.value if request.language_hint else None
    try:
        result = engine.transcribe(audio, language_hint)
    except UnsupportedLanguageError as exc:
        return _pipeline_error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            str(exc),
            stage="transcribe.language",
            status_code=422,
            detail={"language_hint": language_hint},
        )

    total_duration_ms = (
        decode_duration_ms + result.inference_duration_ms + result.postprocess_duration_ms
    )
    return TranscribeResponse(
        text=result.text,
        detected_language=LanguageCode(result.language),
        model_version=engine.model_version,
        duration_ms=total_duration_ms,
        stage_timings=[
            StageTiming(stage="decode", duration_ms=round(decode_duration_ms, 3)),
            StageTiming(stage="inference", duration_ms=round(result.inference_duration_ms, 3)),
            StageTiming(stage="postprocess", duration_ms=round(result.postprocess_duration_ms, 3)),
        ],
        degraded=DegradedMode.ok(),
    )
