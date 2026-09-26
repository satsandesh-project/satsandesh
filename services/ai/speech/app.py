"""
Real ASR service — loads faster-whisper once at startup and serves
POST /v1/transcribe. See services/ai/speech/README.md for how to run it.

wav_pcm16 decodes via the stdlib `wave` module; ogg_opus and mp3 decode via
ffmpeg (see services/ai/speech/engine.py). A missing ffmpeg binary, a failed
decode, or a timeout all become a real PipelineError, not a crash — see
README's "Known open question" for the unresolved webm-vs-ogg container
question this does NOT settle.
"""

from __future__ import annotations

import logging
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from contracts.ai.common import AudioFormat, DegradedMode, StageTiming
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.language import LanguageCode
from contracts.ai.transcribe import TranscribeRequest, TranscribeResponse
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from services.ai.speech.engine import (
    AsrEngine,
    FfmpegDecodeError,
    FfmpegNotFoundError,
    UnsupportedWavError,
    decode_via_ffmpeg,
    decode_wav_pcm16,
)
from services.ai.speech.settings import Settings

logger = logging.getLogger("services.ai.speech.app")

settings = Settings.from_env()
engine = AsrEngine(
    model_name=settings.model_name,
    compute_type=settings.compute_type,
    cpu_threads=settings.cpu_threads,
)

_WARMUP_AUDIO_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tone_2s.wav"

_ready = False


def _resolve_local_path(uri: str) -> Path:
    """Resolve an AudioRef.uri to a local filesystem path.

    Accepts `file://` URIs (including the Windows `file:///C:/...` form) and
    plain filesystem paths, since AudioRef.uri is an unconstrained string per
    contracts/ai/common.py — no storage layer is decided yet.
    """
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        raw_path = unquote(parsed.path)
        if raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path.lstrip("/")  # file:///C:/... -> C:/...
        return Path(raw_path)
    return Path(uri)


def _pipeline_error(
    code: ErrorCode, message: str, stage: str, status_code: int, detail: dict | None = None
) -> JSONResponse:
    error = PipelineError(code=code, message=message, stage=stage, detail=detail)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


async def _startup() -> None:
    global _ready
    engine.load()
    warmup_audio = decode_wav_pcm16(_WARMUP_AUDIO_PATH)
    engine.warm_up(warmup_audio)
    _ready = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(
    title="SatSandesh AI — Speech (real ASR)",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/live")
async def health_live() -> dict:
    """200 the instant the process is up. Touches nothing else."""
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """503 until the model is loaded AND a warm-up inference has completed."""
    if not _ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "model_version": engine.model_version,
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
    result = engine.transcribe(audio, language_hint)

    try:
        detected_language = LanguageCode(result.detected_language)
    except ValueError:
        return _pipeline_error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"detected language {result.detected_language!r} is not one of the "
            "LanguageCode values this contract supports (en/hi/te)",
            stage="transcribe.postprocess",
            status_code=422,
            detail={"detected_language": result.detected_language},
        )

    total_duration_ms = (
        decode_duration_ms + result.inference_duration_ms + result.postprocess_duration_ms
    )

    return TranscribeResponse(
        text=result.text,
        detected_language=detected_language,
        model_version=engine.model_version,
        duration_ms=total_duration_ms,
        stage_timings=[
            StageTiming(stage="decode", duration_ms=round(decode_duration_ms, 3)),
            StageTiming(stage="inference", duration_ms=round(result.inference_duration_ms, 3)),
            StageTiming(stage="postprocess", duration_ms=round(result.postprocess_duration_ms, 3)),
        ],
        degraded=DegradedMode.ok(),
    )
