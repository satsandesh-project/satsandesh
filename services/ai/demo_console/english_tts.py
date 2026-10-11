"""
English text-to-speech for the demo console: Windows' built-in speech engine, nothing else.

DEMO ONLY, WINDOWS ONLY, NOT A PRODUCT DECISION. Render has no English voice, so for a
live demo the console speaks English replies with whatever English voice Windows ships
(System.Speech, run in a PowerShell subprocess). The product needs a licensed Piper English
voice served by render; that is a separate task. On any other OS, or with no English voice
installed, or on any failure, callers fall back to text only.

The reply text never goes into the command line: the script is a fixed string and the text
and output path travel in environment variables, so quotes, `$(...)` or backticks in a reply
are just characters to speak.
"""

from __future__ import annotations

import os
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 1000  # a spoken reply, not a document; also bounds the synthesis time
DEFAULT_TIMEOUT_S = 30.0

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($s.Voice.Culture.Name -notlike 'en-*') {
    $v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'en-*' } | Select-Object -First 1
    if (-not $v) { [Console]::Error.WriteLine('NO_ENGLISH_VOICE'); exit 3 }
    $s.SelectVoice($v.VoiceInfo.Name)
}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:SAT_TTS_OUT, $fmt)
$s.Speak($env:SAT_TTS_TEXT)
$name = $s.Voice.Name
$s.Dispose()
[Console]::Out.WriteLine($name)
"""


class EnglishVoiceUnavailable(Exception):
    """No English voice can be used here (not Windows, none installed, or the engine failed)."""


@dataclass(frozen=True)
class EnglishSpeech:
    voice: str  # the exact voice name the engine reported using
    duration_ms: int


def synthesize(text: str, out_path: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> EnglishSpeech:
    """Speak `text` into a 16 kHz mono 16-bit WAV at `out_path`."""
    if sys.platform != "win32":
        raise EnglishVoiceUnavailable(
            "the demo English voice needs Windows' built-in speech engine"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **_clean_env(),
        "SAT_TTS_TEXT": text,
        "SAT_TTS_OUT": str(out_path),
    }
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise EnglishVoiceUnavailable(
            f"the speech engine took longer than {timeout_s:.0f}s"
        ) from exc
    except OSError as exc:
        raise EnglishVoiceUnavailable(f"could not start PowerShell ({exc})") from exc
    if proc.returncode != 0:
        why = (
            "no English voice is installed" if "NO_ENGLISH_VOICE" in proc.stderr else "engine error"
        )
        raise EnglishVoiceUnavailable(f"the speech engine failed: {why} (exit {proc.returncode})")
    voice = (proc.stdout.strip().splitlines() or [""])[-1].strip()
    try:
        with wave.open(str(out_path), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate()
    except (wave.Error, EOFError, OSError) as exc:
        raise EnglishVoiceUnavailable("the speech engine did not produce a valid WAV") from exc
    if not voice or frames <= 0 or rate <= 0:
        raise EnglishVoiceUnavailable("the speech engine produced no audio")
    return EnglishSpeech(voice=voice, duration_ms=round(frames / rate * 1000))


def _clean_env() -> dict[str, str]:
    # The speech engine needs none of our secrets; do not hand it the Hugging Face token.
    return {k: v for k, v in os.environ.items() if k != "HF_TOKEN"}
