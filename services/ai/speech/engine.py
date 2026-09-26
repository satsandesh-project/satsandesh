"""
faster-whisper loading, warm-up, and audio decoding.

`decode_wav_pcm16` stays stdlib-only (`wave`) — a plain WAV file needs no
external decoder, and anything other than mono/16-bit-PCM/16kHz is rejected
rather than silently resampled or channel-mixed. `decode_via_ffmpeg` handles
everything else (ogg_opus, mp3) by shelling out to the `ffmpeg` binary rather
than a Python binding library, keeping the dependency surface small — ffmpeg
is accepted as an external tool dependency, resolved via `FFMPEG_PATH` or
PATH, never a hardcoded path (see services/ai/speech/README.md).
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import faster_whisper
import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger("services.ai.speech.engine")

EXPECTED_SAMPLE_RATE_HZ = 16000

# Short voice notes are the realistic case here (capped well under a minute),
# and ffmpeg decodes audio that short in well under a second on CPU. 15s gives
# generous headroom for a cold process start on a loaded dev machine while
# still guaranteeing a stuck ffmpeg process can never hang a request forever.
FFMPEG_TIMEOUT_S = 15.0


class FfmpegNotFoundError(RuntimeError):
    """The ffmpeg executable could not be located (FFMPEG_PATH, then PATH)."""


class FfmpegDecodeError(RuntimeError):
    """ffmpeg ran but exited non-zero, or the decode timed out."""


def _ffmpeg_executable() -> str:
    """Resolve the ffmpeg binary: FFMPEG_PATH env override, else plain "ffmpeg"
    relying on PATH. Never a hardcoded absolute path — that would break every
    machine that installs ffmpeg somewhere else (see README for the override).
    """
    return os.environ.get("FFMPEG_PATH", "").strip() or "ffmpeg"


def decode_via_ffmpeg(path: Path, timeout_s: float = FFMPEG_TIMEOUT_S) -> np.ndarray:
    """Decode ogg_opus/mp3 (or anything ffmpeg recognizes) into a mono float32
    array in [-1, 1] at 16kHz — the same shape decode_wav_pcm16 produces.

    The input is already a real file on disk (AudioRef.uri resolved to a local
    path), so it's passed to ffmpeg by path rather than piped over stdin; only
    the decoded PCM comes back over a pipe (stdout), avoiding a temp output
    file. `subprocess.run(capture_output=True)` drains stdout and stderr
    concurrently, so there's no pipe-deadlock risk from ffmpeg's stderr
    logging.

    Raises FileNotFoundError if `path` doesn't exist, FfmpegNotFoundError if
    the ffmpeg binary itself can't be located, and FfmpegDecodeError for a
    non-zero exit, a timeout, or empty output.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")

    ffmpeg_bin = _ffmpeg_executable()
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        str(EXPECTED_SAMPLE_RATE_HZ),
        "pipe:1",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
    except FileNotFoundError as exc:
        raise FfmpegNotFoundError(
            f"ffmpeg executable {ffmpeg_bin!r} was not found (checked FFMPEG_PATH, "
            "then PATH) — install ffmpeg or set FFMPEG_PATH to its binary location"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegDecodeError(f"ffmpeg timed out after {timeout_s}s decoding {path}") from exc

    if result.returncode != 0:
        stderr_tail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise FfmpegDecodeError(f"ffmpeg exited {result.returncode} decoding {path}: {stderr_tail}")

    if not result.stdout:
        raise FfmpegDecodeError(f"ffmpeg produced no audio output decoding {path}")

    pcm16 = np.frombuffer(result.stdout, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0


class UnsupportedWavError(ValueError):
    """The file exists and is a WAV file, but not mono/16-bit-PCM/16kHz."""


def decode_wav_pcm16(path: Path) -> np.ndarray:
    """Decode a plain WAV file into a mono float32 array in [-1, 1] at 16kHz.

    Raises FileNotFoundError if `path` doesn't exist, and UnsupportedWavError
    for anything that isn't already mono/16-bit-PCM/16kHz.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")

    try:
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            frame_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except wave.Error as exc:
        raise UnsupportedWavError(f"not a readable WAV file: {exc}") from exc

    if sample_width != 2:
        raise UnsupportedWavError(
            f"expected 16-bit PCM (sampwidth=2 bytes), got sampwidth={sample_width}"
        )
    if n_channels != 1:
        raise UnsupportedWavError(f"expected mono audio, got {n_channels} channels")
    if frame_rate != EXPECTED_SAMPLE_RATE_HZ:
        raise UnsupportedWavError(
            f"expected {EXPECTED_SAMPLE_RATE_HZ} Hz, got {frame_rate} Hz "
            "(resampling is a later phase, not this one)"
        )

    pcm16 = np.frombuffer(raw, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0


@dataclass
class TranscriptionResult:
    text: str
    detected_language: str
    inference_duration_ms: float
    postprocess_duration_ms: float


class AsrEngine:
    """Owns exactly one faster-whisper model instance, loaded once at startup."""

    def __init__(self, model_name: str, compute_type: str, cpu_threads: int) -> None:
        self._model_name = model_name
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._model: WhisperModel | None = None
        self.load_duration_ms: float | None = None
        self.warmup_duration_ms: float | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_version(self) -> str:
        return (
            f"faster-whisper-{self._model_name}-{self._compute_type}@{faster_whisper.__version__}"
        )

    def load(self) -> None:
        start = time.perf_counter()
        self._model = WhisperModel(
            self._model_name,
            device="cpu",
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
        )
        self.load_duration_ms = (time.perf_counter() - start) * 1000
        logger.info("ASR model loaded: %s (%.1f ms)", self.model_version, self.load_duration_ms)

    def warm_up(self, audio: np.ndarray) -> None:
        if self._model is None:
            raise RuntimeError("warm_up() called before load()")
        start = time.perf_counter()
        segments, _info = self._model.transcribe(audio, language="en")
        list(segments)  # the segments iterator is lazy; force real inference now
        self.warmup_duration_ms = (time.perf_counter() - start) * 1000
        logger.info("ASR warm-up inference complete (%.1f ms)", self.warmup_duration_ms)

    def transcribe(self, audio: np.ndarray, language_hint: str | None) -> TranscriptionResult:
        if self._model is None:
            raise RuntimeError("transcribe() called before load()")

        infer_start = time.perf_counter()
        segments, info = self._model.transcribe(audio, language=language_hint)
        segments = list(segments)
        infer_duration_ms = (time.perf_counter() - infer_start) * 1000

        post_start = time.perf_counter()
        text = "".join(segment.text for segment in segments).strip()
        detected_language = info.language
        postprocess_duration_ms = (time.perf_counter() - post_start) * 1000

        return TranscriptionResult(
            text=text,
            detected_language=detected_language,
            inference_duration_ms=infer_duration_ms,
            postprocess_duration_ms=postprocess_duration_ms,
        )
