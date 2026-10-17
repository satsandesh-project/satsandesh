"""
Optional RNNoise denoising for the ASR services (ASR_DENOISE=off|rnnoise, default off).

Shared by services/ai/speech/ and services/ai/speech_indicconformer/ (the latter
re-exports this module, the same way it re-exports the decode helpers). Only numpy is
imported at module level: `pyrnnoise` is the optional `denoise` extra and is imported
lazily on first use, so a service with ASR_DENOISE=off never needs it.

Pipeline (RNNoise only runs at 48 kHz): 16 kHz -> x3 up -> RNNoise -> /3 down -> 16 kHz.
The x3 / /3 resampling is a small windowed-sinc FIR in numpy, so the extra does not
drag in scipy/soxr.
"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

logger = logging.getLogger("services.ai.speech.denoise")

ALLOWED_DENOISE_MODES = ("off", "rnnoise")
INPUT_SAMPLE_RATE_HZ = 16000
RNNOISE_SAMPLE_RATE_HZ = 48000
_RATIO = RNNOISE_SAMPLE_RATE_HZ // INPUT_SAMPLE_RATE_HZ  # 3


class DenoiseUnavailableError(RuntimeError):
    """ASR_DENOISE=rnnoise but `pyrnnoise` (the `denoise` extra) is not installed."""


class Denoiser(Protocol):
    def __call__(self, audio: np.ndarray) -> np.ndarray: ...


def parse_denoise_mode(raw: str | None) -> str:
    mode = (raw if raw is not None else "off").strip().lower() or "off"
    if mode not in ALLOWED_DENOISE_MODES:
        raise ValueError(f"ASR_DENOISE={raw!r} is not one of {list(ALLOWED_DENOISE_MODES)}")
    return mode


def _lowpass_taps(cutoff_hz: float, sample_rate_hz: float, n_taps: int = 127) -> np.ndarray:
    n = np.arange(n_taps) - (n_taps - 1) / 2
    fc = cutoff_hz / sample_rate_hz
    taps = 2 * fc * np.sinc(2 * fc * n) * np.kaiser(n_taps, 8.0)
    return (taps / taps.sum()).astype(np.float32)


# Cut at 7.5 kHz: just under the 8 kHz Nyquist of the 16 kHz side.
_TAPS_48K = _lowpass_taps(7500.0, RNNOISE_SAMPLE_RATE_HZ)


def upsample_16k_to_48k(audio: np.ndarray) -> np.ndarray:
    stuffed = np.zeros(len(audio) * _RATIO, dtype=np.float32)
    stuffed[::_RATIO] = audio
    return np.convolve(stuffed, _TAPS_48K * _RATIO, mode="same").astype(np.float32)


def downsample_48k_to_16k(audio: np.ndarray) -> np.ndarray:
    filtered = np.convolve(audio, _TAPS_48K, mode="same")
    return filtered[::_RATIO].astype(np.float32)


class RnnoiseDenoiser:
    """float32 [-1, 1] @16 kHz in, float32 [-1, 1] @16 kHz out, same length.

    pyrnnoise contract (checked against pyrnnoise 0.4.5's source): `RNNoise(sample_rate)`
    is a per-stream object (recurrent state), and `denoise_chunk(chunk, partial)` takes a
    `[channels, samples]` int16 array and is a GENERATOR yielding
    `(speech_probs, denoised_frame)` per 10 ms frame, each frame `[channels, n]` int16.
    `partial=True` marks the last chunk: it flushes the tail and resets the stream.
    """

    def __init__(self) -> None:
        try:
            from pyrnnoise import RNNoise
        except ImportError as exc:
            raise DenoiseUnavailableError(
                "ASR_DENOISE=rnnoise needs the optional `pyrnnoise` package: "
                'pip install "satsandesh-ai[denoise]"'
            ) from exc
        self._rnnoise_cls = RNNoise

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        if audio.size == 0:
            return audio
        n_in = len(audio)
        up = upsample_16k_to_48k(audio)
        # [channels=1, samples], int16: the shape/dtype denoise_chunk expects.
        pcm16 = np.clip(up * 32768.0, -32768, 32767).astype(np.int16)[np.newaxis, :]

        # A new RNNoise per clip: it carries state across frames, so sharing one between
        # requests would leak one caller's audio history into another's.
        rnn = self._rnnoise_cls(sample_rate=RNNOISE_SAMPLE_RATE_HZ)
        # Generator: it must be consumed, and every yielded frame kept.
        frames = [np.atleast_2d(frame) for _speech_probs, frame in rnn.denoise_chunk(pcm16, True)]
        if not frames:
            return audio
        denoised = np.concatenate(frames, axis=1)[0]  # join in time, take channel 0 (mono)
        denoised = denoised.astype(np.float32) / 32768.0

        out = downsample_48k_to_16k(denoised)
        # RNNoise works on 10 ms frames; trim/pad so the length contract holds.
        if len(out) >= n_in:
            return out[:n_in]
        return np.pad(out, (0, n_in - len(out)))


_denoisers: dict[str, Denoiser] = {}


def get_denoiser(mode: str) -> Denoiser | None:
    """None for "off"; otherwise a process-wide denoiser (created lazily, once)."""
    if mode == "off":
        return None
    if mode not in _denoisers:
        _denoisers[mode] = RnnoiseDenoiser()
    return _denoisers[mode]
