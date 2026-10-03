"""
Typed startup configuration for the real render service (English pivot text ->
target-language text -> target-language audio; see services/ai/render/README.md).

Same discipline as services/ai/mt/settings.py: visible defaults so a plain
`uvicorn services.ai.render.app:app` works, but a present-and-invalid override
(and a missing HF_TOKEN) raises SettingsError at import time rather than failing
later inside transformers or uvicorn.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_RENDER_DIR = Path(__file__).resolve().parent


class SettingsError(RuntimeError):
    """Missing or invalid services/ai/render configuration."""


def _env_int(name: str, default: str, low: int, high: int | None = None) -> int:
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name}={raw!r} is not an integer") from exc
    if value < low or (high is not None and value > high):
        raise SettingsError(f"{name}={value} is out of range")
    return value


@dataclass(frozen=True)
class Settings:
    hf_token: str
    mt_model_name: str
    mt_device: str
    num_beams: int
    max_length: int
    voice_te: str
    voice_hi: str
    voice_dir: Path
    output_dir: Path
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        # The en-indic model is gated on Hugging Face (auto-approved, but per repo: the
        # indic-en acceptance does NOT carry over). Fail fast here with a clear message
        # instead of a 403 from deep inside from_pretrained. Never hardcoded or committed.
        hf_token = os.environ.get("HF_TOKEN", "").strip()
        if not hf_token:
            raise SettingsError(
                "HF_TOKEN is required (the en-indic MT model is gated on Hugging Face). "
                "Set it in the environment; see services/ai/render/README.md."
            )

        mt_model_name = os.environ.get(
            "RENDER_MT_MODEL_NAME", "ai4bharat/indictrans2-en-indic-dist-200M"
        ).strip()
        if not mt_model_name:
            raise SettingsError("RENDER_MT_MODEL_NAME must not be empty")

        mt_device = os.environ.get("RENDER_MT_DEVICE", "auto").strip()
        if mt_device not in {"auto", "cpu", "cuda"}:
            raise SettingsError(
                f"RENDER_MT_DEVICE={mt_device!r} must be one of 'auto', 'cpu', 'cuda'"
            )

        # Voice ids are the two confirmed in docs/RENDER_ENVIRONMENT_SPIKE.md.
        # hi_IN-rohan-medium: IIT Madras IndicTTS data, commercially-permissive EULA.
        # Swapped from hi_IN-pratham-medium (CC BY-NC-SA 4.0, non-commercial) per
        # review — see services/ai/DECISIONS.md #12 and NOTICE.
        voice_te = os.environ.get("RENDER_VOICE_TE", "te_IN-maya-medium").strip()
        voice_hi = os.environ.get("RENDER_VOICE_HI", "hi_IN-rohan-medium").strip()
        if not voice_te or not voice_hi:
            raise SettingsError("RENDER_VOICE_TE / RENDER_VOICE_HI must not be empty")

        voice_dir = Path(os.environ.get("RENDER_VOICE_DIR", str(_RENDER_DIR / "voices")))
        output_dir = Path(os.environ.get("RENDER_OUTPUT_DIR", str(_RENDER_DIR / "output")))

        # 8005: 8001 mock, 8002 ASR, 8003 moderation, 8004 MT.
        port = _env_int("RENDER_PORT", "8005", 1, 65535)

        return cls(
            hf_token=hf_token,
            mt_model_name=mt_model_name,
            mt_device=mt_device,
            num_beams=_env_int("RENDER_NUM_BEAMS", "5", 1),
            max_length=_env_int("RENDER_MAX_LENGTH", "256", 1),
            voice_te=voice_te,
            voice_hi=voice_hi,
            voice_dir=voice_dir,
            output_dir=output_dir,
            port=port,
        )
