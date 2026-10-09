"""
Demo console — a small local web app that drives the REAL AI pipeline end to
end for a live demo: microphone -> real ASR service -> real MT service.

This is a demo/testing harness, NOT the product UI (that is clients/elder-app,
owned by M1). It talks to services/ai/speech/ and services/ai/mt/ only over
their public HTTP APIs, the same way the gateway eventually will — it never
imports their engines.

Pipeline = PIPELINE_STAGES below: one POST /pipeline/<stage> endpoint per
stage, called one at a time by static/index.html so the page can show
progressive status. A future stage (Week 7 TTS, later a moderation badge) is
one more function + one more PipelineStage entry here, and one more entry in
the page's STAGES array.

Every failure a stage can hit becomes a DemoError -> JSON
{"error": {"stage", "title", "message"}} with a calm, human-readable message
the page shows verbatim. Nothing in here should ever surface a stack trace.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
import time
import wave
from array import array
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from services.ai.mt.settings import Settings as MtSettings
from services.ai.speech.settings import Settings as SpeechSettings

logger = logging.getLogger("services.ai.demo_console")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[demo_console] %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

_HERE = Path(__file__).resolve().parent
STATIC_DIR = _HERE / "static"
# Overwritten on every recording; gitignored (a real person's voice).
RECORDING_PATH = _HERE / "_last_recording.wav"

# 8001 mock, 8002 speech, 8003 moderation, 8004 mt -> 8005 is the next free one.
DEMO_PORT = int(os.environ.get("DEMO_CONSOLE_PORT", "8005"))

# Ports come from the services' own settings modules (env-overridable there),
# so a port change in speech/ or mt/ is picked up here automatically. 127.0.0.1
# rather than "localhost": on Windows, localhost tries ::1 first, and a refused
# IPv6 connect can add ~2s before falling back to IPv4 (uvicorn binds IPv4).
ASR_BASE_URL = f"http://127.0.0.1:{SpeechSettings.from_env().port}"
MT_BASE_URL = f"http://127.0.0.1:{MtSettings.from_env().port}"

# The console has its own render port (render's own default, 8005, is the console's).
# It must not import render's Settings: that raises without HF_TOKEN.
RENDER_PORT = int(os.environ.get("DEMO_RENDER_PORT", "8006"))
RENDER_BASE_URL = f"http://127.0.0.1:{RENDER_PORT}"
# Where render writes its WAVs: same variable and default as render/settings.py.
RENDER_OUTPUT_DIR = Path(
    os.environ.get("RENDER_OUTPUT_DIR", str(_HERE.parent / "render" / "output"))
)
# The only file names GET /pipeline/audio/ will serve (what render's _render_one writes).
AUDIO_NAME_PATTERN = r"^render-[0-9a-f]{32}-(te|hi)\.wav$"
_AUDIO_NAME_RE = re.compile(AUDIO_NAME_PATTERN)

# Browser recording: hard cap, and the upload limits derived from it.
MAX_RECORD_SECONDS = float(os.environ.get("DEMO_MAX_RECORD_SECONDS", "20"))
MAX_UPLOAD_BYTES = int(os.environ.get("DEMO_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)))
UPLOAD_GRACE_S = 1.0  # the page's auto-stop can overshoot the cap by a timer tick
EXPECTED_RATE_HZ = 16000

RECORD_SECONDS = float(os.environ.get("DEMO_RECORD_SECONDS", "7"))
# Peak |sample| (int16) below which a recording counts as silent. A quiet room
# on the dev headset mic measured peak ~12; normal speech peaks in the
# thousands. A muted mic gives exactly 0.
SILENCE_PEAK_THRESHOLD = int(os.environ.get("DEMO_SILENCE_PEAK", "150"))

# CPU-only inference: a 7s clip is a few seconds, but a cold first call can be
# much slower. Generous, but finite — the page must never hang forever.
ASR_TIMEOUT_S = 120.0
MT_TIMEOUT_S = 90.0
RENDER_TIMEOUT_S = 180.0  # translate + synthesize on a cold CPU
HEALTH_TIMEOUT_S = 3.0

SUPPORTED_LANGUAGES = {"te": "Telugu", "hi": "Hindi"}


class DemoError(Exception):
    def __init__(self, title: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.title = title
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------
# Shared HTTP helper for calling the real services
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Upstream:
    name: str  # human name used in messages, e.g. "Speech recognition (ASR)"
    base_url: str
    start_hint: str


ASR = Upstream(
    name="Speech recognition (ASR)",
    base_url=ASR_BASE_URL,
    start_hint="Start it with start_demo.py, or: cd services/ai && "
    "PYTHONPATH=../.. .venv/Scripts/python.exe -m uvicorn speech.app:app --port "
    f"{ASR_BASE_URL.rsplit(':', 1)[1]}",
)
MT = Upstream(
    name="Translation (MT)",
    base_url=MT_BASE_URL,
    start_hint="Start it with start_demo.py, or: cd services/ai && "
    "PYTHONPATH=../.. .venv/Scripts/python.exe -m uvicorn mt.app:app --port "
    f"{MT_BASE_URL.rsplit(':', 1)[1]}",
)
RENDER = Upstream(
    name="Spoken reply (render)",
    base_url=RENDER_BASE_URL,
    start_hint="Start it with start_demo.py (needs HF_TOKEN and the Piper voices)",
)


# trust_env=False on every client: these calls are always to local services, so a
# system/corporate proxy setting must never be able to intercept them.
async def _call_upstream(
    upstream: Upstream, path: str, payload: dict[str, Any], timeout_s: float
) -> dict[str, Any]:
    url = f"{upstream.base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
            resp = await client.post(url, json=payload)
    except httpx.ConnectError as exc:
        raise DemoError(
            f"{upstream.name} service is not running",
            f"Could not connect to {upstream.base_url}. {upstream.start_hint}, "
            "then press Try again.",
            status_code=503,
        ) from exc
    except httpx.TimeoutException as exc:
        raise DemoError(
            f"{upstream.name} took too long",
            f"No answer from {upstream.base_url} within {timeout_s:.0f}s. The service may "
            "be overloaded or stuck — check its terminal/log, then press Try again.",
            status_code=504,
        ) from exc
    except httpx.HTTPError as exc:
        raise DemoError(
            f"{upstream.name} connection problem",
            f"Talking to {upstream.base_url} failed: {type(exc).__name__}: {exc}",
        ) from exc

    try:
        body = resp.json()
    except ValueError:
        body = None

    if resp.status_code == 200 and isinstance(body, dict):
        return body

    upstream_msg = body.get("message") if isinstance(body, dict) else None
    if resp.status_code == 503:
        raise DemoError(
            f"{upstream.name} is still warming up",
            f"The model is still loading. Wait a few seconds and press Try again. "
            f"({upstream_msg or 'HTTP 503'})",
            status_code=503,
        )
    raise DemoError(
        f"{upstream.name} returned an error",
        upstream_msg or f"Unexpected HTTP {resp.status_code} from {url}.",
    )


async def _check_ready(upstream: Upstream) -> dict[str, Any]:
    base = {"name": upstream.name, "url": upstream.base_url}
    try:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_S, trust_env=False) as client:
            resp = await client.get(f"{upstream.base_url}/health/ready")
    except httpx.HTTPError:
        return {**base, "state": "down"}
    if resp.status_code != 200:
        return {**base, "state": "warming_up"}
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    # ASR and MT report model_version; render reports mt_model_version + tts_voices.
    out = {
        **base,
        "state": "ready",
        "model": body.get("model_version") or body.get("mt_model_version"),
    }
    if "tts_voices" in body:
        out["tts_voices"] = body["tts_voices"]
    return out


# --------------------------------------------------------------------------
# Pipeline stages
# --------------------------------------------------------------------------

_record_lock = threading.Lock()


def _record_blocking() -> dict[str, Any]:
    # Imported lazily so the console still starts (and can show a clear error)
    # on a machine without sounddevice/PortAudio installed.
    try:
        import sounddevice as sd
        from services.ai.tools.record_sample import (
            NoInputDeviceError,
            check_input_device_available,
            record_to_file,
        )
    except (ImportError, OSError) as exc:
        raise DemoError(
            "Recording support is not installed",
            "sounddevice/soundfile could not be loaded. Install with: "
            f"pip install -e .[tools]  ({exc})",
            status_code=500,
        ) from exc

    try:
        check_input_device_available()
    except NoInputDeviceError as exc:
        raise DemoError(
            "No microphone found",
            "No usable input device. Plug in / enable the microphone (Windows Sound "
            f"settings -> Input), then press Try again. ({exc})",
            status_code=503,
        ) from exc

    outcome: dict[str, Any] = {}

    def _worker() -> None:
        try:
            outcome["result"] = record_to_file(RECORD_SECONDS, RECORDING_PATH)
        except Exception as exc:  # noqa: BLE001 — reported to the page below
            outcome["error"] = exc

    start = time.perf_counter()
    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    # Watchdog: sd.wait() has no timeout of its own; a wedged driver must not
    # become a silent hang in front of the audience.
    worker.join(RECORD_SECONDS + 10)
    if worker.is_alive():
        try:
            sd.stop()
        except Exception:  # best effort; the DemoError below is what matters
            logger.exception("sd.stop() failed after a recording watchdog timeout")
        raise DemoError(
            "Microphone stopped responding",
            "Recording did not finish in time. Unplug/replug the microphone or pick it "
            "again in Windows Sound settings, then press Try again.",
            status_code=504,
        )
    if "error" in outcome:
        exc = outcome["error"]
        raise DemoError(
            "Recording failed",
            f"The microphone reported an error ({type(exc).__name__}: {exc}). "
            "Check it is connected and not in use by another app, then press Try again.",
            status_code=503,
        )

    result = outcome["result"]
    wall_ms = (time.perf_counter() - start) * 1000
    if result.peak_amplitude < SILENCE_PEAK_THRESHOLD:
        raise DemoError(
            "Recording was silent",
            "The microphone picked up (almost) nothing. Check it is not muted and speak "
            "a little closer, then press Try again.",
            status_code=422,
        )
    logger.info(
        "recorded %.1fs (peak %d) -> %s", result.actual_seconds, result.peak_amplitude, result.path
    )
    return {
        "seconds": round(result.actual_seconds, 2),
        "peak_amplitude": result.peak_amplitude,
        "ran_short": result.ran_short,
        "latency_ms": {"record_wall": round(wall_ms, 1)},
    }


class RecordBody(BaseModel):
    pass


async def stage_record(_body: RecordBody) -> dict[str, Any]:
    if not _record_lock.acquire(blocking=False):
        raise DemoError(
            "Already recording",
            "A recording is already in progress — wait for it to finish.",
            status_code=409,
        )
    try:
        return await asyncio.to_thread(_record_blocking)
    finally:
        _record_lock.release()


class TranscribeBody(BaseModel):
    source_language: str


async def stage_transcribe(body: TranscribeBody) -> dict[str, Any]:
    lang = _require_language(body.source_language)
    if not RECORDING_PATH.exists():
        raise DemoError(
            "Nothing recorded yet", "Press Record first, then try again.", status_code=409
        )
    start = time.perf_counter()
    data = await _call_upstream(
        ASR,
        "/v1/transcribe",
        {
            "audio": {"uri": str(RECORDING_PATH), "format": "wav_pcm16"},
            "language_hint": lang,
        },
        ASR_TIMEOUT_S,
    )
    round_trip_ms = (time.perf_counter() - start) * 1000
    text = (data.get("text") or "").strip()
    if not text:
        raise DemoError(
            "No speech recognised",
            "The recording reached the ASR service, but no words were recognised. "
            "Speak clearly for most of the 7 seconds and press Try again.",
            status_code=422,
        )
    latency = {t["stage"]: t["duration_ms"] for t in data.get("stage_timings", [])}
    latency["service_total"] = data.get("duration_ms")
    latency["round_trip"] = round(round_trip_ms, 1)
    return {
        "text": text,
        "detected_language": data.get("detected_language"),
        "model_version": data.get("model_version"),
        "latency_ms": latency,
    }


class TranslateBody(BaseModel):
    text: str
    source_language: str


async def stage_translate(body: TranslateBody) -> dict[str, Any]:
    lang = _require_language(body.source_language)
    if not body.text.strip():
        raise DemoError("Nothing to translate", "The transcript is empty.", status_code=422)
    start = time.perf_counter()
    data = await _call_upstream(
        MT, "/v1/pivot", {"text": body.text, "source_language": lang}, MT_TIMEOUT_S
    )
    round_trip_ms = (time.perf_counter() - start) * 1000
    return {
        "text": data.get("pivot_text", ""),
        "model_version": data.get("model_version"),
        "latency_ms": {
            "service_total": data.get("duration_ms"),
            "round_trip": round(round_trip_ms, 1),
        },
    }


class RenderBody(BaseModel):
    text: str
    target_language: str


def _audio_path_from_uri(uri: str) -> Path | None:
    """Resolve render's file:// URI to a file inside RENDER_OUTPUT_DIR, or None."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    try:
        path = Path(url2pathname(unquote(parsed.path))).resolve()
        root = RENDER_OUTPUT_DIR.resolve()
    except (OSError, ValueError):
        return None
    if path.parent != root or not _AUDIO_NAME_RE.fullmatch(path.name):
        return None
    return path


async def stage_render(body: RenderBody) -> dict[str, Any]:
    lang = _require_language(body.target_language)
    if not body.text.strip():
        raise DemoError("Nothing to say", "The English text is empty.", status_code=422)
    start = time.perf_counter()
    data = await _call_upstream(
        RENDER,
        "/v1/render",
        {"pivot_text": body.text, "target_languages": [lang]},
        RENDER_TIMEOUT_S,
    )
    round_trip_ms = (time.perf_counter() - start) * 1000
    results = data.get("results") or []
    result = next((r for r in results if r.get("language") == lang), None)
    if result is None:
        raise DemoError(
            "Spoken reply service returned an error",
            f"The render service did not return a {SUPPORTED_LANGUAGES[lang]} result.",
        )
    degraded = result.get("degraded") or {}
    audio = result.get("audio") or {}
    audio_url = None
    if not degraded.get("active"):
        path = _audio_path_from_uri(audio.get("uri") or "")
        if path is None:
            raise DemoError(
                "Spoken reply service returned an error",
                "Render produced audio in an unexpected location. Check that RENDER_OUTPUT_DIR "
                "is the same for the render service and the console, then press Try again.",
            )
        if not path.is_file():
            raise DemoError(
                "Spoken reply audio not found",
                "Render reported audio but the file is not there. Press Try again.",
            )
        audio_url = f"/pipeline/audio/{path.name}"
    return {
        "text": result.get("text", ""),
        "language": lang,
        "audio_url": audio_url,
        "audio_duration_ms": audio.get("duration_ms") if audio_url else None,
        "model_version_translate": result.get("model_version_translate"),
        "model_version_tts": result.get("model_version_tts"),
        "degraded": {
            "active": bool(degraded.get("active")),
            "reason": degraded.get("reason"),
            "detail": degraded.get("detail"),
        },
        "latency_ms": {
            "service_total": result.get("duration_ms"),
            "round_trip": round(round_trip_ms, 1),
        },
    }


def _require_language(code: str) -> str:
    if code not in SUPPORTED_LANGUAGES:
        raise DemoError(
            "Unsupported language",
            f"{code!r} is not supported here; choose Telugu or Hindi.",
            status_code=422,
        )
    return code


@dataclass(frozen=True)
class PipelineStage:
    id: str
    body_model: type[BaseModel]
    run: Callable[[Any], Any]


# Run in this order by the page. "render" is Part 2 (English -> spoken target language);
# the page runs it from its own section, not as part of the Part 1 chain.
PIPELINE_STAGES: list[PipelineStage] = [
    PipelineStage("record", RecordBody, stage_record),
    PipelineStage("transcribe", TranscribeBody, stage_transcribe),
    PipelineStage("translate", TranslateBody, stage_translate),
    PipelineStage("render", RenderBody, stage_render),
]


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------


STARTUP_CHECK_WINDOW_S = 180.0


async def _startup_check() -> None:
    """Log (never block on) whether the real services are reachable and ready,
    so a broken setup shows up in this terminal before the demo starts.

    Polls for a while (start_demo.py launches all three at once, so ASR/MT are
    often still loading when this console comes up), logging each state change
    and a clear final verdict for anything that never became ready.
    """
    last: dict[str, str] = {}
    deadline = time.monotonic() + STARTUP_CHECK_WINDOW_S
    while True:
        statuses = await asyncio.gather(_check_ready(ASR), _check_ready(MT), _check_ready(RENDER))
        for st in statuses:
            if last.get(st["name"]) == st["state"]:
                continue
            last[st["name"]] = st["state"]
            if st["state"] == "ready":
                logger.info(
                    "startup check: %s at %s is READY (%s)", st["name"], st["url"], st.get("model")
                )
            elif st["state"] == "warming_up":
                logger.info(
                    "startup check: %s at %s is up, model still warming up", st["name"], st["url"]
                )
            else:
                logger.info("startup check: %s at %s not reachable yet", st["name"], st["url"])
        if all(st["state"] == "ready" for st in statuses):
            return
        if time.monotonic() > deadline:
            for st in statuses:
                if st["state"] != "ready":
                    logger.warning(
                        "startup check: %s at %s is NOT READY after %.0fs — the pipeline "
                        "will fail at that stage until it is started/ready",
                        st["name"],
                        st["url"],
                        STARTUP_CHECK_WINDOW_S,
                    )
            return
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_startup_check())
    yield
    task.cancel()


app = FastAPI(title="SatSandesh AI — Demo console", version="0.1.0", lifespan=lifespan)


@app.exception_handler(DemoError)
async def _demo_error_handler(request: Request, exc: DemoError) -> JSONResponse:
    stage = request.url.path.rsplit("/", 1)[-1]
    logger.warning("stage %s failed: %s — %s", stage, exc.title, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"stage": stage, "title": exc.title, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    stage = request.url.path.rsplit("/", 1)[-1]
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "stage": stage,
                "title": "Bad request from the page",
                "message": f"The page sent an unexpected request: {exc.errors()}",
            }
        },
    )


@app.exception_handler(Exception)
async def _unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unexpected error on %s", request.url.path)
    stage = request.url.path.rsplit("/", 1)[-1]
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "stage": stage,
                "title": "Something unexpected went wrong",
                "message": f"{type(exc).__name__}: {exc}. Details are in the demo console "
                "terminal. Press Try again.",
            }
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/deps")
async def health_deps() -> dict[str, Any]:
    """Readiness of the real services, for the page's status line."""
    asr, mt, render = await asyncio.gather(
        _check_ready(ASR), _check_ready(MT), _check_ready(RENDER)
    )
    return {
        "asr": asr,
        "mt": mt,
        "render": render,
        "record_seconds": RECORD_SECONDS,
        "max_record_seconds": MAX_RECORD_SECONDS,
    }


def _register(stage: PipelineStage) -> None:
    body_model = stage.body_model

    async def endpoint(body):  # type: ignore[no-untyped-def]
        return await stage.run(body)

    # Set explicitly: with `from __future__ import annotations` a closure-local
    # annotation would be an unresolvable string to FastAPI.
    endpoint.__annotations__ = {"body": body_model, "return": dict[str, Any]}
    app.post(f"/pipeline/{stage.id}", name=f"pipeline_{stage.id}")(endpoint)


for _stage in PIPELINE_STAGES:
    _register(_stage)


def _validate_wav(data: bytes) -> tuple[float, int]:
    """Return (duration_s, peak) for a 16 kHz mono PCM16 WAV, or raise DemoError."""
    if not data:
        raise DemoError("Recording was empty", "The page sent no audio. Press Try again.", 422)
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise DemoError(
            "Audio is not a WAV file",
            "The recording must be a 16 kHz mono 16-bit WAV. Reload the page and press Try again.",
            415,
        )
    try:
        with wave.open(BytesIO(data), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise DemoError(
            "Audio is not a valid WAV file",
            f"The WAV could not be read ({exc}). Press Try again.",
            415,
        ) from exc
    if rate != EXPECTED_RATE_HZ:
        raise DemoError(
            "Wrong sample rate",
            f"Expected 16 kHz audio, got {rate} Hz. Reload the page and press Try again.",
            422,
        )
    if channels != 1:
        raise DemoError(
            "Audio is not mono", f"Expected 1 channel, got {channels}. Press Try again.", 422
        )
    if width != 2:
        raise DemoError(
            "Audio is not 16-bit", f"Expected 16-bit samples, got {width * 8}-bit.", 422
        )
    samples = array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if not samples:
        raise DemoError("Recording was empty", "The WAV contains no audio. Press Try again.", 422)
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max(max(samples), -min(samples))
    return len(samples) / rate, int(peak)


@app.post("/pipeline/upload")
async def pipeline_upload(request: Request) -> dict[str, Any]:
    """Browser microphone path: the page POSTs a 16 kHz mono PCM16 WAV it encoded itself."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise DemoError("Recording is too large", "The upload exceeds the size limit.", 413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise DemoError("Recording is too large", "The upload exceeds the size limit.", 413)
        chunks.append(chunk)
    data = b"".join(chunks)
    duration_s, peak = _validate_wav(data)
    if duration_s > MAX_RECORD_SECONDS + UPLOAD_GRACE_S:
        raise DemoError(
            "Recording is too long",
            f"Recordings are limited to {MAX_RECORD_SECONDS:.0f} seconds. Press Try again.",
            422,
        )
    if peak < SILENCE_PEAK_THRESHOLD:
        raise DemoError(
            "Recording was silent",
            "The microphone picked up (almost) nothing. Check it is not muted and speak "
            "a little closer, then press Try again.",
            422,
        )
    tmp = RECORDING_PATH.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, RECORDING_PATH)
    logger.info("uploaded %.1fs (peak %d)", duration_s, peak)
    return {"duration_s": round(duration_s, 2), "peak": peak}


@app.get("/pipeline/audio/{name}")
async def pipeline_audio(name: str) -> FileResponse:
    """Serve one rendered WAV: whitelisted by name, confined to the render output dir."""
    if _AUDIO_NAME_RE.fullmatch(name):
        root = RENDER_OUTPUT_DIR.resolve()
        path = (root / name).resolve()
        if path.parent == root and path.is_file():
            return FileResponse(path, media_type="audio/wav")
    raise HTTPException(status_code=404, detail="not found")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
