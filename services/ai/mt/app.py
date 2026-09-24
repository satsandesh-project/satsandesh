"""
Real MT pivot service -- loads IndicTrans2 (indic-en, distilled) once at
startup and serves POST /v1/pivot against contracts/ai/pivot.py.

Source-language -> English direction ONLY. The reverse direction
(English -> target language, contracts/ai/render.py) is a separate service,
built alongside TTS in Week 7 -- see services/ai/mt/README.md.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from contracts.ai.common import DegradedMode
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.language import LanguageCode
from contracts.ai.pivot import PivotRequest, PivotResponse
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from services.ai.mt.engine import MtEngine, UnsupportedSourceLanguageError
from services.ai.mt.settings import Settings

logger = logging.getLogger("services.ai.mt.app")

settings = Settings.from_env()
engine = MtEngine(
    model_name=settings.model_name,
    device=settings.device,
    num_beams=settings.num_beams,
    max_length=settings.max_length,
)

# Contract doesn't say what pivot_text should be when source_language is
# already 'en' (contracts/ai/pivot.py has no field or docstring addressing
# it) -- ASSUMPTION, not confirmed contract behavior: passthrough/echo the
# input unchanged. Flagged in README as an open question for the team.
_PASSTHROUGH_MODEL_VERSION = "passthrough (no MT model invoked; source_language == en)"

_WARMUP_TEXT = "नमस्ते"  # Hindi: "Hello" -- short, fixed, always available (no file I/O)
_WARMUP_LANGUAGE = LanguageCode.HINDI

_ready = False


def _pipeline_error(
    code: ErrorCode, message: str, stage: str, status_code: int, detail: dict | None = None
) -> JSONResponse:
    error = PipelineError(code=code, message=message, stage=stage, detail=detail)
    return JSONResponse(status_code=status_code, content=error.model_dump(mode="json"))


async def _startup() -> None:
    global _ready
    engine.load()
    engine.warm_up(_WARMUP_TEXT, _WARMUP_LANGUAGE)
    _ready = True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await _startup()
    yield


app = FastAPI(
    title="SatSandesh AI — MT Pivot (source -> English)",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health/live")
async def health_live() -> dict:
    """200 the instant the process is up. Touches nothing else."""
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """503 until the model is loaded AND a warm-up translation has completed."""
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


@app.post("/v1/pivot", response_model=None)
async def pivot(request: PivotRequest) -> PivotResponse | JSONResponse:
    if not _ready:
        return _pipeline_error(
            ErrorCode.MODEL_LOAD_FAILED,
            "MT model is not ready yet (still loading or warm-up incomplete).",
            stage="pivot.startup",
            status_code=503,
        )

    start = time.perf_counter()

    # Realistic case: ASR produced nothing useful from a silent/noisy
    # recording. Empty-passthrough, not an error -- there is nothing to
    # translate, and failing the request here would just push the same
    # "nothing to say" problem one stage downstream instead of resolving it.
    if not request.text.strip():
        return PivotResponse(
            pivot_text="",
            source_language=request.source_language,
            model_version=_PASSTHROUGH_MODEL_VERSION,
            duration_ms=(time.perf_counter() - start) * 1000,
            degraded=DegradedMode.ok(),
        )

    if request.source_language == LanguageCode.ENGLISH:
        return PivotResponse(
            pivot_text=request.text,
            source_language=request.source_language,
            model_version=_PASSTHROUGH_MODEL_VERSION,
            duration_ms=(time.perf_counter() - start) * 1000,
            degraded=DegradedMode.ok(),
        )

    try:
        result = engine.translate(request.text, request.source_language)
    except UnsupportedSourceLanguageError as exc:
        return _pipeline_error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            str(exc),
            stage="pivot.preprocess",
            status_code=422,
            detail={"source_language": request.source_language.value},
        )

    logger.info(
        "pivot stage timings (ms): preprocess=%.1f inference=%.1f postprocess=%.1f",
        result.preprocess_duration_ms,
        result.inference_duration_ms,
        result.postprocess_duration_ms,
    )

    return PivotResponse(
        pivot_text=result.text,
        source_language=request.source_language,
        model_version=engine.model_version,
        duration_ms=result.total_duration_ms,
        degraded=DegradedMode.ok(),
    )
