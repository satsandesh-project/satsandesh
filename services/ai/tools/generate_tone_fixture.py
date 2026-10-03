"""
Generates services/ai/tests/fixtures/tone_2s.wav: a 2-second, 440Hz sine tone,
mono, 16-bit PCM, 16kHz. Not speech — purely to exercise the decode -> model
-> response pipeline mechanically (used as both the startup warm-up audio and
the mechanical test fixture). Real-speech accuracy is a later phase, once real
Telugu samples are recorded.

    python tools/generate_tone_fixture.py
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE_HZ = 16000
DURATION_S = 2.0
FREQUENCY_HZ = 440.0
AMPLITUDE = 0.2  # keep well under full scale; this is a mechanical fixture, not a loudness test

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tone_2s.wav"


def main() -> None:
    n_samples = int(SAMPLE_RATE_HZ * DURATION_S)
    frames = bytearray()
    for i in range(n_samples):
        t = i / SAMPLE_RATE_HZ
        sample = AMPLITUDE * math.sin(2 * math.pi * FREQUENCY_HZ * t)
        frames += struct.pack("<h", int(sample * 32767))

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(FIXTURE_PATH), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(SAMPLE_RATE_HZ)
        wf.writeframes(bytes(frames))

    print(f"wrote {FIXTURE_PATH} ({n_samples} samples, {DURATION_S}s @ {SAMPLE_RATE_HZ}Hz)")


if __name__ == "__main__":
    main()
