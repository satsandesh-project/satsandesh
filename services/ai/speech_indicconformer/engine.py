"""
IndicConformer model loading, warm-up and inference.

Audio decoding is NOT reimplemented here: `decode_wav_pcm16` (stdlib `wave`) and
`decode_via_ffmpeg` (ogg_opus/mp3) are re-exported from services/ai/speech/engine.py,
so both ASR services accept exactly the same formats. `torchaudio.load()` / torchcodec
are deliberately not used (broken against this machine's FFmpeg build; see the spike doc).

Note on sharing: importing services.ai.speech.engine also imports faster_whisper at
module level. That works because faster-whisper is a base dependency of this package,
but it couples this service to the fallback's module. If speech/ is ever removed, the
decode helpers should move to a shared module (e.g. services/ai/common/audio.py).

The model takes an explicit language code and does NOT auto-detect, and it covers Indian
languages only -- so only "hi" and "te" from the contract's en/hi/te are usable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import numpy as np
from services.ai.speech.engine import (  # noqa: F401  (re-exported for app.py)
    FfmpegDecodeError,
    FfmpegNotFoundError,
    UnsupportedWavError,
    decode_via_ffmpeg,
    decode_wav_pcm16,
)

logger = logging.getLogger("services.ai.speech_indicconformer.engine")

SUPPORTED_LANGUAGES = frozenset({"hi", "te"})


class UnsupportedLanguageError(ValueError):
    """Language is missing or not one the model can be asked to transcribe."""


@dataclass
class TranscriptionResult:
    text: str
    language: str
    decode_mode: str
    inference_duration_ms: float
    postprocess_duration_ms: float


class IndicConformerEngine:
    """Owns exactly one loaded IndicConformer model, loaded once at startup."""

    def __init__(self, model_name: str, hf_token: str, decode_mode: str) -> None:
        self._model_name = model_name
        self._hf_token = hf_token
        self.decode_mode = decode_mode
        self._model = None
        self.load_duration_ms: float | None = None
        self.warmup_duration_ms: float | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def model_version(self) -> str:
        return f"{self._model_name}-onnx-{self.decode_mode}"

    def load(self) -> None:
        # Heavy imports are local so importing this module (settings/format tests) is cheap.
        from transformers import AutoModel

        start = time.perf_counter()
        self._model = AutoModel.from_pretrained(
            self._model_name, trust_remote_code=True, token=self._hf_token
        )
        self.load_duration_ms = (time.perf_counter() - start) * 1000
        logger.info("ASR model loaded: %s (%.1f ms)", self.model_version, self.load_duration_ms)

    def warm_up(self, audio: np.ndarray) -> None:
        if self._model is None:
            raise RuntimeError("warm_up() called before load()")
        start = time.perf_counter()
        self._run(audio, "hi", self.decode_mode)
        self.warmup_duration_ms = (time.perf_counter() - start) * 1000
        logger.info("ASR warm-up inference complete (%.1f ms)", self.warmup_duration_ms)

    def _run(self, audio: np.ndarray, language: str, decode_mode: str) -> str:
        import torch

        wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).unsqueeze(0)
        with torch.no_grad():
            out = self._model(wav, language, decode_mode)  # type: ignore[misc]
        if isinstance(out, (list, tuple)):
            out = out[0] if out else ""
        return str(out)

    def transcribe(
        self, audio: np.ndarray, language: str | None, decode_mode: str | None = None
    ) -> TranscriptionResult:
        if self._model is None:
            raise RuntimeError("transcribe() called before load()")
        if language is None or language not in SUPPORTED_LANGUAGES:
            raise UnsupportedLanguageError(
                f"language {language!r} is not supported; this model needs an explicit "
                f"language hint and supports only {sorted(SUPPORTED_LANGUAGES)} of the "
                "contract's languages (no auto-detection, no English)"
            )
        mode = decode_mode or self.decode_mode

        infer_start = time.perf_counter()
        raw = self._run(audio, language, mode)
        infer_duration_ms = (time.perf_counter() - infer_start) * 1000

        post_start = time.perf_counter()
        text = raw.strip()
        postprocess_duration_ms = (time.perf_counter() - post_start) * 1000

        return TranscriptionResult(
            text=text,
            language=language,
            decode_mode=mode,
            inference_duration_ms=infer_duration_ms,
            postprocess_duration_ms=postprocess_duration_ms,
        )
