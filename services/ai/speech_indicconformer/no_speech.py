"""
No-speech guard for IndicConformer.

Evidence (PR description, "Part A"): in CTC mode the model emits non-empty text for
3 s of digital silence (te "అ", hi "అ है") and for white noise (te "అ"). It has no
"no speech" output, so this guard runs before inference and returns empty text for
input that cannot be speech. Empty text is valid in TranscribeResponse.

Two cheap checks, numpy only, no model:
- too quiet: whole-clip RMS below `min_rms` (default 0.003, about -50 dBFS);
- noise-like: averaged spectral flatness above `max_flatness` (default 0.5). White
  noise is ~1.0; speech and tones are far below it.

Deliberately conservative: it only catches input that is clearly not speech. Quiet
real speech and speech in coloured noise still go to the model.
"""

from __future__ import annotations

import numpy as np

DEFAULT_MIN_RMS = 0.003
DEFAULT_MAX_FLATNESS = 0.5
_FRAME = 512


def spectral_flatness(audio: np.ndarray) -> float:
    """Geometric / arithmetic mean of the frame-averaged power spectrum, in [0, 1]."""
    n_frames = len(audio) // _FRAME
    if n_frames == 0:
        return 0.0
    frames = audio[: n_frames * _FRAME].reshape(n_frames, _FRAME) * np.hanning(_FRAME)
    power = (np.abs(np.fft.rfft(frames, axis=1)) ** 2).mean(axis=0)[1:] + 1e-12
    return float(np.exp(np.mean(np.log(power))) / np.mean(power))


def is_no_speech(
    audio: np.ndarray,
    min_rms: float = DEFAULT_MIN_RMS,
    max_flatness: float = DEFAULT_MAX_FLATNESS,
) -> bool:
    if audio.size == 0:
        return True
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    if rms < min_rms:
        return True
    return spectral_flatness(audio) > max_flatness


def measure(audio: np.ndarray) -> tuple[float, float]:
    """(rms, spectral_flatness) -- what the gate decides on, for logging."""
    if audio.size == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return rms, spectral_flatness(audio)
