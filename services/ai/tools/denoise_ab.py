"""
A/B a recording through a running ASR service with denoise off vs on, side by side.

    python services/ai/tools/denoise_ab.py sample.wav --language te
    python services/ai/tools/denoise_ab.py sample.wav --url http://localhost:8002 --language hi

The service must have ASR_DENOISE=off (the default), so the "off" run is the raw audio.
The "on" run denoises the file HERE with the same RNNoise path the services use
(services/ai/speech/denoise.py, needs the `denoise` extra), writes it to a temporary
16 kHz WAV, and sends that -- so one running service answers both, no restart. Service
and tool must share a filesystem, because AudioRef.uri is a local path.

Run from the repo root with PYTHONPATH=. (same as the services).
"""

from __future__ import annotations

import argparse
import json
import tempfile
import textwrap
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np
from services.ai.speech.denoise import get_denoiser
from services.ai.speech.engine import UnsupportedWavError, decode_via_ffmpeg, decode_wav_pcm16

SAMPLE_RATE_HZ = 16000


def load_audio(path: Path) -> np.ndarray:
    """Mono float32 @16 kHz: stdlib wave for 16 kHz mono PCM16 WAV, ffmpeg otherwise."""
    try:
        return decode_wav_pcm16(path)
    except (UnsupportedWavError, wave.Error):
        return decode_via_ffmpeg(path)


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE_HZ)
        wf.writeframes(pcm.tobytes())


def transcribe(url: str, wav_path: Path, language: str | None, timeout_s: float) -> str:
    payload: dict = {"audio": {"uri": str(wav_path.resolve()), "format": "wav_pcm16"}}
    if language:
        payload["language_hint"] = language
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/transcribe",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read())["text"]
    except urllib.error.HTTPError as exc:
        return f"<HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}>"


def side_by_side(off: str, on: str, width: int = 44) -> str:
    left = textwrap.wrap(off or "(empty)", width) or [""]
    right = textwrap.wrap(on or "(empty)", width) or [""]
    rows = max(len(left), len(right))
    left += [""] * (rows - len(left))
    right += [""] * (rows - len(right))
    lines = [f"{'denoise OFF':<{width}} | denoise ON", f"{'-' * width}-+-{'-' * width}"]
    lines += [f"{a:<{width}} | {b}" for a, b in zip(left, right, strict=True)]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--url", default="http://localhost:8002", help="ASR service base URL")
    parser.add_argument("--language", choices=["en", "hi", "te"], default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    audio = load_audio(args.audio)
    denoiser = get_denoiser("rnnoise")
    assert denoiser is not None
    cleaned = denoiser(audio)

    with tempfile.TemporaryDirectory() as tmp:
        raw_path, clean_path = Path(tmp) / "raw.wav", Path(tmp) / "denoised.wav"
        write_wav(raw_path, audio)
        write_wav(clean_path, cleaned)
        off = transcribe(args.url, raw_path, args.language, args.timeout)
        on = transcribe(args.url, clean_path, args.language, args.timeout)

    print(side_by_side(off, on))


if __name__ == "__main__":
    main()
