"""
Side-by-side ASR comparison for choosing the Telugu ASR path (GitHub issue #98, Q3).

Evidence-gathering tooling only: it is not imported by any service, changes no
routing and no contract. It has NO ground truth, so it makes no accuracy claim --
read the transcripts yourself and fill in the empty "my verdict" column.

For every WAV in a directory it runs, in-process and one engine at a time (each
model is loaded once, then released before the next):

  - faster-whisper small  int8   (the service default; services/ai/speech/engine.AsrEngine)
  - faster-whisper medium int8
  - IndicConformer CTC, and RNNT  (services/ai/speech_indicconformer/engine; needs HF_TOKEN
    in the environment -- read from there only, never written anywhere)

each with denoise off, and also on if services/ai/speech/denoise.py exists and
pyrnnoise imports (otherwise that column is skipped, with a message). Every file is
run twice per engine/denoise combination: the very first inference after a model load
is labelled "cold", everything else "warm".

    cd services/ai
    $env:PYTHONPATH = "..\\.."            # repo root on the path (PowerShell)
    $env:HF_TOKEN = "hf_..."              # only needed for the IndicConformer rows
    .\\.venv\\Scripts\\python.exe tools/asr_compare.py C:\\path\\to\\wavs --lang te

Writes results.csv and results.md under tools/asr_compare_out/ (gitignored).
First use of each model downloads its weights (medium is the largest).
"""

from __future__ import annotations

import argparse
import csv
import importlib
import os
import platform
import sys
import time
import wave
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TARGET_SAMPLE_RATE_HZ = 16000
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "asr_compare_out"
ALL_ENGINES = ("whisper-small", "whisper-medium", "indic-ctc", "indic-rnnt")

# ---- script detection ---------------------------------------------------------------

_TELUGU = (0x0C00, 0x0C7F)
_DEVANAGARI_RANGES = ((0x0900, 0x097F), (0xA8E0, 0xA8FF))
_LATIN_RANGES = ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F))


def _script_of(ch: str) -> str | None:
    cp = ord(ch)
    if _TELUGU[0] <= cp <= _TELUGU[1]:
        return "Telugu"
    if any(lo <= cp <= hi for lo, hi in _DEVANAGARI_RANGES):
        return "Devanagari"
    if any(lo <= cp <= hi for lo, hi in _LATIN_RANGES):
        return "Latin"
    return None


def detect_script(text: str) -> str:
    """Classify a transcript by Unicode block: "Telugu", "Devanagari", "Latin",
    "mixed" (letters from more than one of those), or "none" (empty, or only
    digits/punctuation/other scripts). Digits, spaces and punctuation are ignored.
    The wrong-script check: a Telugu request that comes back "Devanagari" or "Latin".
    """
    found = {s for s in (_script_of(c) for c in text) if s is not None}
    if not found:
        return "none"
    if len(found) == 1:
        return next(iter(found))
    return "mixed"


# ---- audio --------------------------------------------------------------------------


def resample_to_16k(audio: np.ndarray, sample_rate_hz: int) -> np.ndarray:
    """FFT-based resample (numpy only -- scipy/soxr are not dependencies of this
    package). Band-limited, fine for speech comparison; not studio quality."""
    if sample_rate_hz == TARGET_SAMPLE_RATE_HZ or audio.size == 0:
        return audio
    n_out = max(1, round(audio.size * TARGET_SAMPLE_RATE_HZ / sample_rate_hz))
    spectrum = np.fft.rfft(audio.astype(np.float64))
    out = np.fft.irfft(spectrum, n=n_out) * (n_out / audio.size)
    return out.astype(np.float32)


def read_wav_16k_mono(path: Path) -> tuple[np.ndarray, str | None]:
    """Return (float32 mono 16 kHz audio, note). `note` says what was changed, or None.

    Raises ValueError with a clear message for anything it will not silently alter
    (multi-channel, unsupported sample width / float WAV).
    """
    try:
        with wave.open(str(path), "rb") as wf:
            channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
            raw = wf.readframes(wf.getnframes())
    except wave.Error as exc:
        raise ValueError(f"not a readable PCM WAV ({exc}); convert to 16-bit PCM first") from exc
    if channels != 1:
        raise ValueError(f"{channels} channels; this tool wants mono and will not mix down")
    if width == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif width == 1:
        audio = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"{width * 8}-bit WAV is not supported; convert to 16-bit PCM first")
    if rate == TARGET_SAMPLE_RATE_HZ:
        return audio, None
    return (
        resample_to_16k(audio, rate),
        f"resampled {rate} Hz -> {TARGET_SAMPLE_RATE_HZ} Hz (FFT, numpy)",
    )


# ---- results + writers --------------------------------------------------------------


@dataclass
class Row:
    file: str
    engine: str
    mode: str
    denoise: str  # "on" | "off"
    run: int  # 1 or 2 for this file/engine/denoise
    state: str  # "cold" (first inference after model load) | "warm"
    wall_s: float  # denoise (if on) + ASR
    audio_s: float
    rtf: float  # wall_s / audio_s
    transcript: str
    script: str
    model_version: str
    my_verdict: str = ""


def make_row(
    *,
    file: str,
    engine: str,
    mode: str,
    denoise: bool,
    run: int,
    cold: bool,
    wall_s: float,
    audio_s: float,
    transcript: str,
    model_version: str,
) -> Row:
    return Row(
        file=file,
        engine=engine,
        mode=mode,
        denoise="on" if denoise else "off",
        run=run,
        state="cold" if cold else "warm",
        wall_s=round(wall_s, 3),
        audio_s=round(audio_s, 3),
        rtf=round(wall_s / audio_s, 3) if audio_s > 0 else 0.0,
        transcript=transcript,
        script=detect_script(transcript),
        model_version=model_version,
    )


_COLUMNS = [f.name for f in fields(Row)]


def write_csv(rows: Iterable[Row], path: Path) -> None:
    # utf-8-sig so Excel shows Telugu/Devanagari correctly when double-clicked.
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(rows: list[Row], header_lines: list[str]) -> str:
    out = ["# ASR comparison", ""]
    out += [f"- {line}" for line in header_lines]
    out += [
        "",
        (
            "No ground truth: nothing here says which engine is more accurate. "
            "`my verdict` is yours to fill in."
        ),
        "",
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "|".join("---" for _ in _COLUMNS) + "|",
    ]
    for row in rows:
        values = asdict(row)
        out.append("| " + " | ".join(_md_cell(values[c]) for c in _COLUMNS) + " |")
    return "\n".join(out) + "\n"


def write_markdown(rows: list[Row], header_lines: list[str], path: Path) -> None:
    path.write_text(render_markdown(rows, header_lines), encoding="utf-8")


# ---- environment --------------------------------------------------------------------


def cpu_model() -> str:
    try:
        if sys.platform == "win32":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.is_file():
            for line in cpuinfo.read_text(errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:  # noqa: BLE001, S110 - informational only
        pass
    return platform.processor() or "unknown"


def _version(module: str) -> str:
    try:
        return str(getattr(importlib.import_module(module), "__version__", "unknown"))
    except Exception:  # noqa: BLE001
        return "not installed"


def load_denoise() -> tuple[Callable[[np.ndarray], np.ndarray] | None, str]:
    """(denoise_fn, message). Needs services/ai/speech/denoise.py exposing
    `denoise(float32 mono 16 kHz) -> float32 same length`, and pyrnnoise importable."""
    try:
        module = importlib.import_module("services.ai.speech.denoise")
    except ImportError:
        return None, "denoise column skipped: services/ai/speech/denoise.py does not exist"
    fn = getattr(module, "denoise", None)
    if not callable(fn):
        return None, "denoise column skipped: denoise.py has no `denoise(audio)` function"
    try:
        importlib.import_module("pyrnnoise")
    except ImportError:
        return None, "denoise column skipped: pyrnnoise is not installed"
    return fn, "denoise column included (RNNoise via services.ai.speech.denoise)"


# ---- engines ------------------------------------------------------------------------


@dataclass
class LoadedEngine:
    name: str
    mode: str
    model_version: str
    transcribe: Callable[[np.ndarray], str]
    load_s: float
    fresh: bool = True  # False when another LoadedEngine already warmed this same model


def _load_whisper(size: str, lang: str, threads: int) -> LoadedEngine:
    from services.ai.speech.engine import AsrEngine

    engine = AsrEngine(size, "int8", threads)
    engine.load()
    return LoadedEngine(
        name=f"whisper-{size}",
        mode="-",
        model_version=engine.model_version,
        transcribe=lambda audio: engine.transcribe(audio, lang).text,
        load_s=(engine.load_duration_ms or 0.0) / 1000,
    )


def _load_indic(modes: list[str], lang: str) -> list[LoadedEngine]:
    from services.ai.speech_indicconformer.engine import IndicConformerEngine
    from services.ai.speech_indicconformer.settings import DEFAULT_MODEL_NAME

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HF_TOKEN is not set in the environment")
    engine = IndicConformerEngine(DEFAULT_MODEL_NAME, token, modes[0])
    engine.load()
    return [
        LoadedEngine(
            name="indicconformer",
            mode=mode,
            model_version=f"{DEFAULT_MODEL_NAME}-onnx-{mode}",
            transcribe=lambda audio, m=mode: engine.transcribe(audio, lang, m).text,
            load_s=(engine.load_duration_ms or 0.0) / 1000,
            fresh=(i == 0),
        )
        for i, mode in enumerate(modes)
    ]


def run_engine(
    eng: LoadedEngine,
    clips: list[tuple[str, np.ndarray]],
    denoise_fn: Callable[[np.ndarray], np.ndarray] | None,
    runs: int,
    on_row: Callable[[Row], None],
) -> None:
    """Run every clip `runs` times with denoise off (and on, if available). The first
    inference after the model load is the only one labelled cold."""
    first = eng.fresh
    variants = [False] + ([True] if denoise_fn is not None else [])
    for name, audio in clips:
        for use_denoise in variants:
            for run in range(1, runs + 1):
                start = time.perf_counter()
                data = denoise_fn(audio) if use_denoise and denoise_fn else audio
                text = eng.transcribe(data)
                wall = time.perf_counter() - start
                on_row(
                    make_row(
                        file=name,
                        engine=eng.name,
                        mode=eng.mode,
                        denoise=use_denoise,
                        run=run,
                        cold=first,
                        wall_s=wall,
                        audio_s=audio.size / TARGET_SAMPLE_RATE_HZ,
                        transcript=text,
                        model_version=eng.model_version,
                    )
                )
                first = False


# ---- main ---------------------------------------------------------------------------


def _load_clips(directory: Path) -> list[tuple[str, np.ndarray]]:
    clips = []
    for path in sorted(directory.glob("*.wav")):
        try:
            audio, note = read_wav_16k_mono(path)
        except ValueError as exc:
            print(f"SKIP {path.name}: {exc}")
            continue
        if note:
            print(f"NOTE {path.name}: {note}")
        clips.append((path.name, audio))
    return clips


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("wav_dir", type=Path, help="directory of mono WAV files")
    parser.add_argument("--lang", choices=["te", "hi"], default="te")
    parser.add_argument(
        "--engines",
        default=",".join(ALL_ENGINES),
        help=f"comma list from: {', '.join(ALL_ENGINES)} (default: all)",
    )
    parser.add_argument("--runs", type=int, default=2, help="runs per file/variant (default 2)")
    parser.add_argument("--threads", type=int, default=4, help="whisper cpu_threads (default 4)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in ALL_ENGINES]
    if unknown:
        parser.error(f"unknown engine(s): {unknown}")
    if not args.wav_dir.is_dir():
        parser.error(f"not a directory: {args.wav_dir}")

    clips = _load_clips(args.wav_dir)
    if not clips:
        print("no usable .wav files found")
        return 1

    denoise_fn, denoise_msg = load_denoise()
    print(denoise_msg)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[Row] = []
    notes: list[str] = [denoise_msg]
    header = [
        f"date: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"CPU: {cpu_model()} ({os.cpu_count()} logical cores)",
        f"whisper cpu_threads: {args.threads}; IndicConformer threads: torch default",
        f"language hint: {args.lang}; files: {len(clips)}; runs per variant: {args.runs}",
        (
            f"faster-whisper {_version('faster_whisper')}, torch {_version('torch')}, "
            f"transformers {_version('transformers')}"
        ),
        "cold = first inference after that model load; every other run is warm",
    ]

    def flush(load_lines: list[str]) -> None:
        write_csv(rows, args.out_dir / "results.csv")
        write_markdown(rows, header + notes + load_lines, args.out_dir / "results.md")

    load_lines: list[str] = []
    groups: list[tuple[str, Callable[[], list[LoadedEngine]]]] = []
    for size in ("small", "medium"):
        if f"whisper-{size}" in wanted:
            groups.append(
                (f"whisper-{size}", lambda s=size: [_load_whisper(s, args.lang, args.threads)])
            )
    indic_modes = [m for m in ("ctc", "rnnt") if f"indic-{m}" in wanted]
    if indic_modes:
        groups.append(("indicconformer", lambda: _load_indic(indic_modes, args.lang)))

    for label, loader in groups:
        print(f"loading {label} ...")
        try:
            engines = loader()
        except Exception as exc:  # noqa: BLE001 - report and continue with the next engine
            msg = f"{label}: SKIPPED, could not load ({type(exc).__name__}: {exc})"
            print(msg)
            load_lines.append(msg)
            flush(load_lines)
            continue
        for eng in engines:
            load_lines.append(
                f"{eng.name}{'/' + eng.mode if eng.mode != '-' else ''}: "
                f"{eng.model_version}, model load {eng.load_s:.1f}s"
            )
            print(f"running {eng.name} {eng.mode} ...")
            run_engine(eng, clips, denoise_fn, args.runs, rows.append)
            flush(load_lines)
        del engines

    print(f"wrote {args.out_dir / 'results.csv'} and {args.out_dir / 'results.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
