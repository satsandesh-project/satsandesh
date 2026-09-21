"""
faster-whisper loading, warm-up, and plain-WAV decoding.

Decoding here is deliberately stdlib-only (`wave`), not PyAV/ffmpeg. ffmpeg is
confirmed not installed on this dev machine (see docs/AI_LANE_INVENTORY.md), and
a plain WAV file needs no external decoder to read. Anything other than
mono/16-bit-PCM/16kHz is rejected rather than silently resampled or
channel-mixed — resampling is real signal processing that belongs in its own
phase, not something to guess at here.
"""

from __future__ import annotations

import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import faster_whisper
import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger("services.ai.speech.engine")

EXPECTED_SAMPLE_RATE_HZ = 16000


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
