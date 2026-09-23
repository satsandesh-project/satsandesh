"""
Typed startup configuration for the real MT pivot service (source-language ->
English only; see services/ai/mt/README.md).

Same discipline as services/ai/speech/settings.py: visible, documented
defaults so a plain `uvicorn services.ai.mt.app:app` works out of the box, but
a present-and-invalid override raises SettingsError at import time rather than
failing later inside transformers or uvicorn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(RuntimeError):
    """Missing or invalid services/ai/mt configuration."""


@dataclass(frozen=True)
class Settings:
    model_name: str
    device: str
    num_beams: int
    max_length: int
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        model_name = os.environ.get("MT_MODEL_NAME", "ai4bharat/indictrans2-indic-en-dist-200M").strip()
        if not model_name:
            raise SettingsError("MT_MODEL_NAME must not be empty")

        # "auto" resolves to cuda if available, else cpu, at load time (see
        # engine.py) -- this dev machine has no CUDA GPU (confirmed via
        # torch.cuda.is_available() during the Week 6 Phase 0/1 spike), so the
        # default must work on CPU alone.
        device = os.environ.get("MT_DEVICE", "auto").strip()
        if device not in {"auto", "cpu", "cuda"}:
            raise SettingsError(f"MT_DEVICE={device!r} must be one of 'auto', 'cpu', 'cuda'")

        num_beams_raw = os.environ.get("MT_NUM_BEAMS", "5")
        try:
            num_beams = int(num_beams_raw)
        except ValueError as exc:
            raise SettingsError(f"MT_NUM_BEAMS={num_beams_raw!r} is not an integer") from exc
        if num_beams < 1:
            raise SettingsError(f"MT_NUM_BEAMS must be >= 1, got {num_beams}")

        max_length_raw = os.environ.get("MT_MAX_LENGTH", "256")
        try:
            max_length = int(max_length_raw)
        except ValueError as exc:
            raise SettingsError(f"MT_MAX_LENGTH={max_length_raw!r} is not an integer") from exc
        if max_length < 1:
            raise SettingsError(f"MT_MAX_LENGTH must be >= 1, got {max_length}")

        # 8004: 8001 is the mock (services/ai/mock/app.py), 8002 is the real
        # ASR service (services/ai/speech/, feat/ai-speech-asr-w5, not yet
        # merged), 8003 is already claimed by services/ai/moderation/ on main
        # (MOD_PORT default) -- 8004 is the next free port.
        port_raw = os.environ.get("MT_PORT", "8004")
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise SettingsError(f"MT_PORT={port_raw!r} is not an integer") from exc
        if not (1 <= port <= 65535):
            raise SettingsError(f"MT_PORT must be a valid TCP port, got {port}")

        return cls(
            model_name=model_name,
            device=device,
            num_beams=num_beams,
            max_length=max_length,
            port=port,
        )
