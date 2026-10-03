"""
Typed, fail-fast startup configuration for the IndicConformer ASR service.

Same shape as services/ai/speech/settings.py: a present-but-invalid value raises
SettingsError instead of failing later inside onnxruntime. HF_TOKEN is required
(the model is gated on HuggingFace) and has no default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL_NAME = "ai4bharat/indic-conformer-600m-multilingual"
ALLOWED_DECODE_MODES = {"ctc", "rnnt"}


class SettingsError(RuntimeError):
    """Missing or invalid services/ai/speech_indicconformer configuration."""


@dataclass(frozen=True)
class Settings:
    hf_token: str
    model_name: str
    decode_mode: str
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        hf_token = os.environ.get("HF_TOKEN", "").strip()
        if not hf_token:
            raise SettingsError(
                "HF_TOKEN is required: the ai4bharat/indic-conformer-600m-multilingual model "
                "is gated on HuggingFace (accept its terms, then use a read token)"
            )

        model_name = os.environ.get("ASR_MODEL_NAME", DEFAULT_MODEL_NAME).strip()
        if not model_name:
            raise SettingsError("ASR_MODEL_NAME must not be empty")

        # Default "ctc": the spike measured it faster (2.1s vs RNNT's 3.1s on a 14.5s
        # clip). Which is more ACCURATE is UNDECIDED -- the spike saw them differ on a
        # few words and had no ground truth to pick one. Flip to "rnnt" via DECODE_MODE
        # to compare; do not treat "ctc" as a measured accuracy winner.
        decode_mode = os.environ.get("DECODE_MODE", "ctc").strip().lower()
        if decode_mode not in ALLOWED_DECODE_MODES:
            raise SettingsError(
                f"DECODE_MODE={decode_mode!r} is not one of {sorted(ALLOWED_DECODE_MODES)}"
            )

        # 8004: 8001 mock, 8002 faster-whisper ASR, 8003 moderation.
        port_raw = os.environ.get("ASR_PORT", "8004")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise SettingsError(f"ASR_PORT={port_raw!r} is not an integer") from exc
        if not (1 <= port <= 65535):
            raise SettingsError(f"ASR_PORT must be a valid TCP port, got {port}")

        return cls(hf_token=hf_token, model_name=model_name, decode_mode=decode_mode, port=port)
