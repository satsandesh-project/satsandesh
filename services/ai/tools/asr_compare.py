"""
Side-by-side ASR comparison for choosing the Telugu/Hindi ASR path (GitHub issue #98, Q3).

Evidence-gathering tooling only: not imported by any service, changes no routing and no
contract. It has NO ground truth, so it makes no accuracy claim -- read the transcripts
yourself and fill in the empty "my verdict" column.

For each audio file it runs, in-process and one engine at a time (each model is loaded
once, then released before the next):

  - IndicConformer CTC and RNNT   (services/ai/speech_indicconformer/engine)
  - faster-whisper small int8     (services/ai/speech/engine.AsrEngine), as the REFERENCE

`--denoise` adds a denoise-on row for every engine (RNNoise via services/ai/speech/denoise.py,
needs the optional `denoise` extra; its time is included in the row's latency).

Each row repeats the file `--runs` times (default 3). The first run is labelled cold when it
is the first inference after that model load. p50/p90 are reported ONLY when runs >= 5;
below that a percentile is not meaningful, so you get the individual run times instead.
The script columns are the share of letters in the expected script (te -> Telugu,
hi -> Devanagari), in Devanagari, and in Latin: a Telugu request answered in Devanagari or
Latin is a wrong-script result.

The tools run the raw engines: no no-speech gate, no service wrapper.

    cd services/ai
    $env:PYTHONPATH = "..\\.."                    # repo root on the path (PowerShell)
    $env:HF_HUB_OFFLINE = "1"                     # use the locally cached models
    .\\.venv\\Scripts\\python.exe tools/asr_compare.py a.wav b.opus --lang te --out cmp.md

IndicConformer needs HF_TOKEN in the environment for a first download (read from there only,
never written anywhere). With HF_HUB_OFFLINE=1 and the weights cached, no token is needed.

The Markdown table goes to stdout (progress goes to stderr); `--out` also writes it to a
file. Do not commit the output: it contains transcripts of real recordings.
"""

from __future__ import annotations

import argparse
import importlib
import os
import platform
import sys
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

TARGET_SAMPLE_RATE_HZ = 16000
ALL_ENGINES = ("indic-ctc", "indic-rnnt", "whisper-small")
PERCENTILE_MIN_RUNS = 5
EXPECTED_SCRIPT = {"te": "Telugu", "hi": "Devanagari"}

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
    """Classify a transcript by Unicode block: "Telugu", "Devanagari", "Latin", "mixed"
    (letters from more than one of those) or "none". Digits/punctuation are ignored."""
    found = {s for s in (_script_of(c) for c in text) if s is not None}
    if not found:
        return "none"
    return next(iter(found)) if len(found) == 1 else "mixed"


def script_shares(text: str) -> dict[str, float | None]:
    """Share (0-100) of the LETTERS in `text` that are Telugu / Devanagari / Latin.
    Letters of any other script count in the denominator. None for all three when the
    text has no letters (an empty transcript), so "0%" never means "nothing came back"."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return {"Telugu": None, "Devanagari": None, "Latin": None}
    counts = {"Telugu": 0, "Devanagari": 0, "Latin": 0}
    for c in letters:
        script = _script_of(c)
        if script is not None:
            counts[script] += 1
    return {k: round(100.0 * v / len(letters), 1) for k, v in counts.items()}


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:g}%"


# ---- audio --------------------------------------------------------------------------


def resample_to_16k(audio: np.ndarray, sample_rate_hz: int) -> np.ndarray:
    """FFT-based resample (numpy only). Band-limited, fine for comparison; not studio quality."""
    if sample_rate_hz == TARGET_SAMPLE_RATE_HZ or audio.size == 0:
        return audio
    n_out = max(1, round(audio.size * TARGET_SAMPLE_RATE_HZ / sample_rate_hz))
    spectrum = np.fft.rfft(audio.astype(np.float64))
    out = np.fft.irfft(spectrum, n=n_out) * (n_out / audio.size)
    return out.astype(np.float32)


def read_wav_16k_mono(path: Path) -> tuple[np.ndarray, str | None]:
    """(float32 mono 16 kHz audio, note). `note` says what was changed, or None. Raises
    ValueError for anything it will not silently alter (multi-channel, odd sample width)."""
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


def load_audio(path: Path) -> tuple[np.ndarray, str | None]:
    """.wav via the stdlib reader; anything else (opus/ogg/webm/mp3...) via the same
    ffmpeg path the services use (already mono float32 16 kHz)."""
    if path.suffix.lower() == ".wav":
        return read_wav_16k_mono(path)
    from services.ai.speech.engine import FfmpegDecodeError, FfmpegNotFoundError, decode_via_ffmpeg

    try:
        return decode_via_ffmpeg(path), f"decoded {path.suffix} via ffmpeg"
    except (FfmpegDecodeError, FfmpegNotFoundError, FileNotFoundError) as exc:
        raise ValueError(str(exc)) from exc


# ---- results ------------------------------------------------------------------------


@dataclass
class Result:
    """All runs of one file on one engine/denoise variant."""

    file: str
    engine: str
    mode: str
    denoise: bool
    audio_s: float
    run_times_s: list[float]
    first_is_cold: bool
    transcripts: list[str]
    model_version: str
    lang: str = "te"
    my_verdict: str = field(default="", init=False)

    @property
    def transcript(self) -> str:
        return self.transcripts[0] if self.transcripts else ""

    @property
    def consistent(self) -> bool:
        return len(set(self.transcripts)) <= 1

    def percentiles(self) -> tuple[float, float] | None:
        """(p50, p90) in seconds, only when there are enough runs for it to mean anything."""
        if len(self.run_times_s) < PERCENTILE_MIN_RUNS:
            return None
        p50, p90 = np.percentile(self.run_times_s, [50, 90])
        return float(p50), float(p90)


def run_engine(
    eng: LoadedEngine,
    clips: list[tuple[str, np.ndarray]],
    denoise_fn: Callable[[np.ndarray], np.ndarray] | None,
    runs: int,
    lang: str,
    on_result: Callable[[Result], None],
) -> None:
    """Every clip x (denoise off, and on if `denoise_fn`) x `runs`. The very first inference
    after the model load is the only one that is cold."""
    first_overall = eng.fresh
    variants = [False] + ([True] if denoise_fn is not None else [])
    for name, audio in clips:
        for use_denoise in variants:
            times: list[float] = []
            texts: list[str] = []
            for _ in range(runs):
                start = time.perf_counter()
                data = denoise_fn(audio) if use_denoise and denoise_fn else audio
                texts.append(eng.transcribe(data))
                times.append(time.perf_counter() - start)
            on_result(
                Result(
                    file=name,
                    engine=eng.name,
                    mode=eng.mode,
                    denoise=use_denoise,
                    audio_s=audio.size / TARGET_SAMPLE_RATE_HZ,
                    run_times_s=times,
                    first_is_cold=first_overall,
                    transcripts=texts,
                    model_version=eng.model_version,
                    lang=lang,
                )
            )
            first_overall = False


# ---- markdown -----------------------------------------------------------------------


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_markdown(results: list[Result], header_lines: list[str], lang: str, runs: int) -> str:
    expected = EXPECTED_SCRIPT[lang]
    show_pct = runs >= PERCENTILE_MIN_RUNS
    cols = ["file", "engine", "denoise", "first run (s)", "all runs (s)"]
    if show_pct:
        cols += ["p50 (s)", "p90 (s)"]
    cols += [
        "RTF",
        f"{expected} %",
        "Devanagari %",
        "Latin %",
        "transcript",
        "stable",
        "my verdict",
    ]

    out = ["# ASR comparison", ""] + [f"- {line}" for line in header_lines]
    out += [
        "",
        (
            "No ground truth: nothing here says which engine is more accurate. faster-whisper "
            "small is the reference, not the answer. `my verdict` is yours to fill in."
        ),
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "|".join("---" for _ in cols) + "|",
    ]
    for r in results:
        shares = script_shares(r.transcript)
        engine = r.engine + (f"/{r.mode}" if r.mode != "-" else "")
        first = f"{r.run_times_s[0]:.2f}" + (" (cold)" if r.first_is_cold else "")
        cells = [
            r.file,
            engine,
            "on" if r.denoise else "off",
            first,
            " / ".join(f"{t:.2f}" for t in r.run_times_s),
        ]
        if show_pct:
            p = r.percentiles()
            cells += [f"{p[0]:.2f}", f"{p[1]:.2f}"] if p else ["-", "-"]
        mean_s = sum(r.run_times_s) / len(r.run_times_s)
        cells += [
            f"{mean_s / r.audio_s:.2f}" if r.audio_s > 0 else "-",
            _pct(shares[expected]),
            _pct(shares["Devanagari"]),
            _pct(shares["Latin"]),
            r.transcript or "(empty)",
            "yes" if r.consistent else "NO: runs differ",
            r.my_verdict,
        ]
        out.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    return "\n".join(out) + "\n"


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
    except OSError:
        pass
    return platform.processor() or "unknown"


def _version(module: str) -> str:
    try:
        return str(getattr(importlib.import_module(module), "__version__", "unknown"))
    except ImportError:
        return "not installed"


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


def _hf_token() -> str:
    """HF_TOKEN from the environment, read here and never printed or written. With
    HF_HUB_OFFLINE=1 the cached weights need no token, so a dummy non-secret is used."""
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        return token
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        return "offline-no-token"
    raise RuntimeError("HF_TOKEN is not set (or set HF_HUB_OFFLINE=1 to use the cached model)")


def _load_indic(modes: list[str], lang: str) -> list[LoadedEngine]:
    from services.ai.speech_indicconformer.engine import IndicConformerEngine
    from services.ai.speech_indicconformer.settings import DEFAULT_MODEL_NAME

    engine = IndicConformerEngine(DEFAULT_MODEL_NAME, _hf_token(), modes[0])
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


def get_denoise_fn() -> Callable[[np.ndarray], np.ndarray]:
    """The services' RNNoise denoiser. Raises with an actionable message if pyrnnoise is
    missing (--denoise was asked for explicitly, so do not silently skip it)."""
    from services.ai.speech.denoise import DenoiseUnavailableError, get_denoiser

    try:
        denoiser = get_denoiser("rnnoise")
    except DenoiseUnavailableError as exc:
        raise SystemExit(f"--denoise: {exc}") from exc
    assert denoiser is not None
    return denoiser


# ---- main ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("audio", type=Path, nargs="+", help="one or more audio files")
    parser.add_argument("--lang", choices=["te", "hi"], required=True)
    parser.add_argument("--runs", type=int, default=3, help="runs per file/variant (default 3)")
    parser.add_argument("--denoise", action="store_true", help="also run every engine denoised")
    parser.add_argument(
        "--engines",
        default=",".join(ALL_ENGINES),
        help=f"comma list from: {', '.join(ALL_ENGINES)} (default: all)",
    )
    parser.add_argument("--threads", type=int, default=4, help="whisper cpu_threads (default 4)")
    parser.add_argument("--out", type=Path, default=None, help="also write the Markdown here")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in ALL_ENGINES]
    if unknown:
        parser.error(f"unknown engine(s): {unknown}")

    clips: list[tuple[str, np.ndarray]] = []
    for path in args.audio:
        try:
            audio, note = load_audio(path)
        except ValueError as exc:
            _log(f"SKIP {path.name}: {exc}")
            continue
        if note:
            _log(f"NOTE {path.name}: {note}")
        clips.append((path.name, audio))
    if not clips:
        _log("no usable audio files")
        return 1

    denoise_fn = get_denoise_fn() if args.denoise else None

    header = [
        f"date: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"CPU: {cpu_model()} ({os.cpu_count()} logical cores)",
        (
            f"language: {args.lang}; files: {len(clips)}; runs per variant: {args.runs}; "
            f"denoise column: {'yes' if args.denoise else 'no'}"
        ),
        (
            f"faster-whisper {_version('faster_whisper')}, torch {_version('torch')}, "
            f"transformers {_version('transformers')}"
        ),
        "cold = first inference after that model load; other runs are warm. "
        + (
            "p50/p90 over all runs."
            if args.runs >= PERCENTILE_MIN_RUNS
            else f"p50/p90 omitted: needs --runs >= {PERCENTILE_MIN_RUNS}."
        ),
    ]

    groups: list[tuple[str, Callable[[], list[LoadedEngine]]]] = []
    indic_modes = [m for m in ("ctc", "rnnt") if f"indic-{m}" in wanted]
    if indic_modes:
        groups.append(("indicconformer", lambda: _load_indic(indic_modes, args.lang)))
    if "whisper-small" in wanted:
        groups.append(("whisper-small", lambda: [_load_whisper("small", args.lang, args.threads)]))

    results: list[Result] = []
    load_lines: list[str] = []
    for label, loader in groups:
        _log(f"loading {label} ...")
        try:
            engines = loader()
        except Exception as exc:  # noqa: BLE001 - report it and carry on with the next engine
            msg = f"{label}: SKIPPED, could not load ({type(exc).__name__}: {exc})"
            _log(msg)
            load_lines.append(msg)
            continue
        for eng in engines:
            load_lines.append(
                f"{eng.name}{'/' + eng.mode if eng.mode != '-' else ''}: "
                f"{eng.model_version}, model load {eng.load_s:.1f}s"
            )
            _log(f"running {eng.name} {eng.mode} ...")
            run_engine(eng, clips, denoise_fn, args.runs, args.lang, results.append)
        del engines

    table = render_markdown(results, header + load_lines, args.lang, args.runs)
    print(table)
    if args.out:
        args.out.write_text(table, encoding="utf-8")
        _log(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
