"""
Standalone CLI to record real voice samples for the Week 5 ASR bake-off.

Not part of the transcribe service — services/ai/speech/ never imports this,
and a deployed ASR service never touches a microphone. This exists purely so
a human can capture real Telugu/Hindi speech on their own machine, saved in
exactly the shape services/ai/speech/engine.py's decode_wav_pcm16() already
expects (mono, 16-bit PCM, 16kHz) so a later bake-off runner can feed these
files straight into the transcribe service with no conversion step.

    python tools/record_sample.py --label telugu_01 --seconds 30

Recordings are saved under services/ai/bakeoff/samples/, which is gitignored
(see bakeoff/samples/.gitignore) — real voice recordings of a real person are
personal data and must never be committed. See bakeoff/README.md for what
samples the bake-off actually needs.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE_HZ = 16000
CHANNELS = 1
DEFAULT_SECONDS = 30

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "bakeoff" / "samples"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--label",
        required=True,
        help="Sample name, used as the filename: bakeoff/samples/<label>.wav",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_SECONDS,
        help=f"Recording duration in seconds (default: {DEFAULT_SECONDS})",
    )
    return parser.parse_args(argv)


def _check_input_device_available() -> None:
    try:
        device_index = sd.default.device[0]
        if device_index is None:
            raise sd.PortAudioError("no default input device")
        sd.query_devices(device_index, "input")
    except (sd.PortAudioError, ValueError) as exc:
        print(f"error: no audio input device is available: {exc}", file=sys.stderr)
        sys.exit(1)


def _resolve_output_path(label: str) -> Path:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SAMPLES_DIR / f"{label}.wav"
    if output_path.exists():
        print(
            f"error: {output_path} already exists — refusing to overwrite a previous "
            "recording. Pick a different --label or delete the old file yourself.",
            file=sys.stderr,
        )
        sys.exit(1)
    return output_path


def _countdown() -> None:
    for n in (3, 2, 1):
        print(f"Recording in {n}...")
        time.sleep(1)
    print("Speak now.")


def record(seconds: float) -> np.ndarray:
    """Record `seconds` of audio, raising sd.PortAudioError on a device failure
    (disconnect, driver hiccup, etc.) rather than returning a silently-partial
    buffer — sd.rec()/sd.wait() either fill the requested frame count or raise.
    """
    n_frames = int(seconds * SAMPLE_RATE_HZ)
    audio = sd.rec(n_frames, samplerate=SAMPLE_RATE_HZ, channels=CHANNELS, dtype="int16")
    sd.wait()
    return audio


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    _check_input_device_available()
    output_path = _resolve_output_path(args.label)

    _countdown()
    try:
        audio = record(args.seconds)
    except sd.PortAudioError as exc:
        print(f"error: recording failed (device hiccup?): {exc}", file=sys.stderr)
        sys.exit(1)

    requested_frames = int(args.seconds * SAMPLE_RATE_HZ)
    actual_frames = audio.shape[0]
    actual_seconds = actual_frames / SAMPLE_RATE_HZ

    sf.write(output_path, audio, SAMPLE_RATE_HZ, subtype="PCM_16")

    if actual_frames < requested_frames:
        print(
            f"WARNING: recording ran short — requested {args.seconds:.1f}s but only "
            f"{actual_seconds:.1f}s was actually captured (possible device hiccup). "
            "Check this file before trusting it as a full sample."
        )
    print(f"Recording complete: saved {actual_seconds:.1f}s to {output_path}")


if __name__ == "__main__":
    main()
