"""
Typed startup configuration for the real ASR service.

Values have visible, code-level defaults (documented per-field below) so a
plain `uvicorn services.ai.speech.app:app` works out of the box on a dev
machine. What this module refuses to do is accept a *present but invalid*
override silently — an unparseable or out-of-range value raises SettingsError
at import time, before the model load even starts, instead of failing later
with a confusing error from deep inside ctranslate2 or uvicorn.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from services.ai.speech.denoise import parse_denoise_mode
from services.ai.speech.engine import HINT_MODES

_ALLOWED_COMPUTE_TYPES = {"int8", "int8_float16", "float16", "float32"}


class SettingsError(RuntimeError):
    """Missing or invalid services/ai/speech configuration."""


@dataclass(frozen=True)
class Settings:
    model_name: str
    compute_type: str
    cpu_threads: int
    port: int
    vad_filter: bool = True
    no_speech_threshold: float = 0.6
    denoise_mode: str = "off"
    # force: a language_hint is decoded as given (today's behaviour). tiebreak: the hint is
    # used unless another of en/hi/te is more probable by more than hint_override_margin.
    hint_mode: str = "force"
    hint_override_margin: float = 0.5

    @classmethod
    def from_env(cls) -> Settings:
        model_name = os.environ.get("ASR_MODEL_NAME", "small").strip()
        if not model_name:
            raise SettingsError("ASR_MODEL_NAME must not be empty")

        compute_type = os.environ.get("ASR_COMPUTE_TYPE", "int8").strip()
        if compute_type not in _ALLOWED_COMPUTE_TYPES:
            raise SettingsError(
                f"ASR_COMPUTE_TYPE={compute_type!r} is not one of {sorted(_ALLOWED_COMPUTE_TYPES)}"
            )

        # Default of 4: this runs on a dev laptop, not a dedicated inference
        # box, and the laptop has no CUDA GPU (see docs/AI_LANE_INVENTORY.md) —
        # pinning every CPU core to one inference call would starve the OS and
        # whatever else is running. 4 threads is faster-whisper's own commonly
        # cited middle ground for a consumer CPU; override via ASR_CPU_THREADS
        # on a machine with a different core budget.
        cpu_threads_raw = os.environ.get("ASR_CPU_THREADS", "4")
        try:
            cpu_threads = int(cpu_threads_raw)
        except ValueError as exc:
            raise SettingsError(f"ASR_CPU_THREADS={cpu_threads_raw!r} is not an integer") from exc
        if cpu_threads < 1:
            raise SettingsError(f"ASR_CPU_THREADS must be >= 1, got {cpu_threads}")

        # 8002: the mock server (services/ai/mock/app.py) already owns 8001
        # (see services/ai/README.md quickstart). Picking the next port keeps
        # both runnable side by side during development.
        port_raw = os.environ.get("ASR_PORT", "8002")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise SettingsError(f"ASR_PORT={port_raw!r} is not an integer") from exc
        if not (1 <= port <= 65535):
            raise SettingsError(f"ASR_PORT must be a valid TCP port, got {port}")

        vad_raw = os.environ.get("ASR_VAD_FILTER", "true").strip().lower()
        if vad_raw not in {"true", "false"}:
            raise SettingsError(f"ASR_VAD_FILTER={vad_raw!r} is not one of ['false', 'true']")

        thr_raw = os.environ.get("ASR_NO_SPEECH_THRESHOLD", "0.6")
        try:
            no_speech_threshold = float(thr_raw)
        except ValueError as exc:
            raise SettingsError(f"ASR_NO_SPEECH_THRESHOLD={thr_raw!r} is not a number") from exc
        if not (0.0 <= no_speech_threshold <= 1.0):
            raise SettingsError(
                f"ASR_NO_SPEECH_THRESHOLD must be in [0, 1], got {no_speech_threshold}"
            )

        try:
            denoise_mode = parse_denoise_mode(os.environ.get("ASR_DENOISE"))
        except ValueError as exc:
            raise SettingsError(str(exc)) from exc

        hint_mode = os.environ.get("ASR_HINT_MODE", "force").strip().lower()
        if hint_mode not in HINT_MODES:
            raise SettingsError(f"ASR_HINT_MODE={hint_mode!r} is not one of {sorted(HINT_MODES)}")

        margin_raw = os.environ.get("ASR_HINT_OVERRIDE_MARGIN", "0.5")
        try:
            hint_override_margin = float(margin_raw)
        except ValueError as exc:
            raise SettingsError(f"ASR_HINT_OVERRIDE_MARGIN={margin_raw!r} is not a number") from exc
        if math.isnan(hint_override_margin) or not (0.0 <= hint_override_margin <= 1.0):
            raise SettingsError(
                f"ASR_HINT_OVERRIDE_MARGIN must be in [0, 1], got {hint_override_margin}"
            )

        return cls(
            model_name=model_name,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            port=port,
            vad_filter=vad_raw == "true",
            no_speech_threshold=no_speech_threshold,
            denoise_mode=denoise_mode,
            hint_mode=hint_mode,
            hint_override_margin=hint_override_margin,
        )
