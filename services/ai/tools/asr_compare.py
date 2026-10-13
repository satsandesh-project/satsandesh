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

With reference transcripts the tool also scores them (WER and CER per row, then a summary):

    .\\.venv\\Scripts\\python.exe tools/asr_compare.py --audio-dir clips --refs references.csv

`--audio-dir` takes every .wav in a folder and reads the language from the filename prefix
(te_ / hi_ / en_); ns_* clips are non-speech and must come back empty. See services/ai/README.md.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import os
import platform
import re
import sys
import time
import unicodedata
import wave
from collections.abc import Callable, Sequence
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
EXPECTED_SCRIPT = {"te": "Telugu", "hi": "Devanagari", "en": "Latin"}
LANGS = ("te", "hi", "en")

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


# ---- scoring against reference transcripts ------------------------------------------

SCORED, NON_SPEECH_OK, HALLUCINATED, NO_REF = "scored", "non-speech ok", "hallucinated", "no ref"
_LANG_PREFIX = re.compile(r"(te|hi|en)_")
_NS_LANG_PREFIX = re.compile(r"ns_(te|hi|en)_")


@dataclass
class Score:
    """WER/CER of one transcript. wer/cer are None unless status == "scored" (or the
    non-speech case, where they are 0.0). `hyp` is kept for the hallucination message."""

    status: str
    wer: float | None = None
    cer: float | None = None
    hyp: str = ""


def normalize(text: str, lang: str) -> str:
    """NFC; punctuation (any Unicode P* category, so also the danda and double danda) becomes
    a space and apostrophes are dropped; whitespace collapsed; casefolded for English ONLY.
    Digits are left exactly as typed (no Telugu/Devanagari <-> ASCII conversion)."""
    text = unicodedata.normalize("NFC", text).replace("'", "").replace("’", "")
    text = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)
    if lang == "en":
        text = text.casefold()
    return " ".join(text.split())


def edit_distance(a: Sequence[str], b: Sequence[str]) -> int:
    """Levenshtein distance (substitution, insertion, deletion all cost 1)."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def score_pair(ref: str, hyp: str, lang: str) -> Score:
    """WER (whitespace-split words) and CER (Unicode code points after NFC, spaces included)
    of `hyp` against `ref`, both normalised first. Rates are not capped at 1.0 (insertions
    can push them over). An empty reference is the non-speech case: an empty hypothesis is
    fine, any text is a hallucination."""
    ref_n, hyp_n = normalize(ref, lang), normalize(hyp, lang)
    if not ref_n:
        return Score(NON_SPEECH_OK, 0.0, 0.0) if not hyp_n else Score(HALLUCINATED, hyp=hyp_n)
    wer = edit_distance(ref_n.split(), hyp_n.split()) / len(ref_n.split())
    cer = edit_distance(ref_n, hyp_n) / len(ref_n)
    return Score(SCORED, wer, cer, hyp_n)


def read_refs(path: Path) -> tuple[dict[str, tuple[str, str]], str | None]:
    """references.csv (filename,language,reference_text; UTF-8 with or without BOM) ->
    ({filename: (language, reference_text)}, problem). A missing/unusable file gives an empty
    mapping and a message, never an exception: the run carries on with every row "no ref"."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            fields = [(f or "").strip().lower() for f in (reader.fieldnames or [])]
            if "filename" not in fields or "reference_text" not in fields:
                return {}, (
                    f"{path}: needs columns filename,language,reference_text "
                    f"(found: {', '.join(fields) or 'none'})"
                )
            reader.fieldnames = fields
            refs: dict[str, tuple[str, str]] = {}
            for row in reader:
                name = Path((row.get("filename") or "").strip()).name
                if name:
                    refs[name] = (
                        (row.get("language") or "").strip().lower(),
                        row["reference_text"] or "",
                    )
            return refs, None
    except FileNotFoundError:
        return {}, f"references file not found: {path}"
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return {}, f"could not read {path} ({type(exc).__name__}: {exc})"


def is_nonspeech(name: str) -> bool:
    return name.lower().startswith("ns_")


def clip_language(name: str, forced: str | None, from_dir: bool) -> str | None:
    """Language of a clip. te_/hi_/en_ prefix, or ns_<lang>_ for non-speech. For a file found
    in --audio-dir the prefix wins and --lang is only the fallback; for a file named on the
    command line --lang wins, as it always did."""
    low = name.lower()
    m = (_NS_LANG_PREFIX if is_nonspeech(low) else _LANG_PREFIX).match(low)
    inferred = m.group(1) if m else None
    return (inferred or forced) if from_dir else (forced or inferred)


def _tokens(name: str) -> list[str]:
    return re.split(r"[_\-.\s]+", Path(name).stem.lower())


def length_bucket(name: str) -> str:
    """short / med / long, from a filename token; "other" when there is none."""
    return next((b for b in ("short", "med", "long") if b in _tokens(name)), "other")


def condition(name: str) -> str:
    return "noisy" if "_noisy" in name.lower() else "clean"


def _engine_label(r: Result) -> str:
    return r.engine + (f"/{r.mode}" if r.mode != "-" else "") + (" +denoise" if r.denoise else "")


def _mean_pct(values: list[float]) -> str:
    return f"{100 * sum(values) / len(values):.1f}%" if values else "-"


def _md_table(cols: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    return out + ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in rows]


def render_summary(results: list[Result]) -> str:
    """Markdown averages of WER/CER per (language, engine), per length bucket and per
    condition. Unweighted means over clips; clips with no reference are excluded and counted;
    non-speech clips are counted (ok / hallucinated) but never averaged."""
    scored = [r for r in results if r.score is not None]
    if not scored:
        return ""
    keys = list(dict.fromkeys((r.lang, _engine_label(r)) for r in scored))

    def stats(rows: list[Result]) -> tuple[str, str, int]:
        ok = [r.score for r in rows if r.score and r.score.status == SCORED]
        return (
            _mean_pct([s.wer for s in ok if s.wer is not None]),
            _mean_pct([s.cer for s in ok if s.cer is not None]),
            len(ok),
        )

    def count(rows: list[Result], status: str) -> int:
        return sum(1 for r in rows if r.score and r.score.status == status)

    per_engine = []
    for lang, eng in keys:
        rows = [r for r in scored if (r.lang, _engine_label(r)) == (lang, eng)]
        wer, cer, n = stats(rows)
        per_engine.append(
            [lang, eng, str(n), str(count(rows, NO_REF)), str(count(rows, NON_SPEECH_OK)),
             str(count(rows, HALLUCINATED)), wer, cer]
        )  # fmt: skip
    out = [
        "## Summary",
        "",
        (
            "Unweighted mean over clips. Clips with no reference are excluded from the averages "
            "(counted in `no ref`); non-speech (`ns_*`) clips are counted, never averaged."
        ),
        "",
        *_md_table(
            ["lang", "engine", "scored", "no ref (excluded)", "non-speech ok", "hallucinated",
             "mean WER", "mean CER"],
            per_engine,
        ),
    ]  # fmt: skip
    speech = [r for r in scored if not is_nonspeech(r.file)]
    for title, label, fn, order in (
        ("By length bucket", "bucket", length_bucket, ("short", "med", "long", "other")),
        ("By condition", "condition", condition, ("clean", "noisy")),
    ):
        rows_out = []
        for lang, eng in keys:
            for value in order:
                rows = [
                    r for r in speech
                    if (r.lang, _engine_label(r)) == (lang, eng) and fn(r.file) == value
                ]  # fmt: skip
                if rows:
                    wer, cer, n = stats(rows)
                    rows_out.append([lang, eng, value, str(n), wer, cer])
        out += ["", f"### {title}", "", *_md_table(
            ["lang", "engine", label, "scored", "mean WER", "mean CER"], rows_out
        )]  # fmt: skip
    return "\n".join(out) + "\n"


def _score_cells(score: Score | None) -> tuple[str, str]:
    if score is None or score.status == NO_REF:
        return "no ref", "no ref"
    if score.status == NON_SPEECH_OK:
        return "empty ok", "-"
    if score.status == HALLUCINATED:
        return f"HALLUCINATED: {score.hyp}", "-"
    assert score.wer is not None and score.cer is not None
    return f"{100 * score.wer:.1f}%", f"{100 * score.cer:.1f}%"


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
    score: Score | None = field(default=None, init=False)  # set only when scoring is on

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


def _table_lines(results: list[Result], lang: str, runs: int) -> list[str]:
    expected = EXPECTED_SCRIPT[lang]
    show_pct = runs >= PERCENTILE_MIN_RUNS
    show_score = any(r.score is not None for r in results)
    # English: the expected script IS Latin, so do not repeat that column.
    share_cols = (
        [expected, "Devanagari"] if expected == "Latin" else [expected, "Devanagari", "Latin"]
    )
    cols = ["file", "engine", "denoise", "first run (s)", "all runs (s)"]
    if show_pct:
        cols += ["p50 (s)", "p90 (s)"]
    cols += ["RTF", *(f"{c} %" for c in share_cols)]
    if show_score:
        cols += ["WER", "CER"]
    cols += ["transcript", "stable", "my verdict"]

    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
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
        cells += [f"{mean_s / r.audio_s:.2f}" if r.audio_s > 0 else "-"]
        cells += [_pct(shares[c]) for c in share_cols]
        if show_score:
            cells += _score_cells(r.score)
        cells += [
            r.transcript or "(empty)",
            "yes" if r.consistent else "NO: runs differ",
            r.my_verdict,
        ]
        out.append("| " + " | ".join(_md_cell(c) for c in cells) + " |")
    return out


def render_markdown(results: list[Result], header_lines: list[str], lang: str, runs: int) -> str:
    """One table, or one table per language (each under its own heading) when the results
    span several. `lang` is used when the results do not say otherwise."""
    langs = list(dict.fromkeys(r.lang for r in results))
    out = ["# ASR comparison", ""] + [f"- {line}" for line in header_lines]
    out += [
        "",
        (
            "No ground truth: nothing here says which engine is more accurate. faster-whisper "
            "small is the reference, not the answer. `my verdict` is yours to fill in."
        ),
        "",
    ]
    if len(langs) <= 1:
        out += _table_lines(results, lang, runs)
    else:
        for lg in langs:
            out += [
                f"## {lg}",
                "",
                *_table_lines([r for r in results if r.lang == lg], lg, runs),
                "",
            ]
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
    parser.add_argument("audio", type=Path, nargs="*", help="audio files (or use --audio-dir)")
    parser.add_argument(
        "--lang",
        choices=LANGS,
        default=None,
        help="language of the files named on the command line (and the fallback for --audio-dir "
        "files with no te_/hi_/en_ prefix). 'en' runs faster-whisper only.",
    )
    parser.add_argument(
        "--audio-dir",
        type=Path,
        default=None,
        help="run every .wav in this folder; language from the filename prefix te_/hi_/en_, "
        "ns_* = non-speech (must come back empty)",
    )
    parser.add_argument(
        "--refs",
        type=Path,
        default=None,
        help="references.csv (filename,language,reference_text): adds WER/CER and a summary",
    )
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


@dataclass
class ClipInfo:
    lang: str
    nonspeech: bool
    ref: str | None  # None = no usable reference; ignored for non-speech clips


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    unknown = [e for e in wanted if e not in ALL_ENGINES]
    if unknown:
        parser.error(f"unknown engine(s): {unknown}")
    if not args.audio and args.audio_dir is None:
        parser.error("give audio files and --lang, or --audio-dir")
    if args.audio_dir is not None and not args.audio_dir.is_dir():
        parser.error(f"--audio-dir {args.audio_dir} is not a directory")
    if args.audio and args.lang is None:
        bad = [p.name for p in args.audio if clip_language(p.name, None, False) is None]
        if bad:
            parser.error(f"--lang is required (no te_/hi_/en_ prefix on: {', '.join(bad)})")

    scoring = args.refs is not None or args.audio_dir is not None
    refs: dict[str, tuple[str, str]] = {}
    refs_problem: str | None = None
    if args.refs is not None:
        refs, refs_problem = read_refs(args.refs)
        if refs_problem:
            _log(f"REFS: {refs_problem}; every row will show 'no ref'")

    sources = [(p, False) for p in args.audio]
    if args.audio_dir is not None:
        sources += [(p, True) for p in sorted(args.audio_dir.glob("*.wav"))]

    clips_by_lang: dict[str, list[tuple[str, np.ndarray]]] = {}
    infos: dict[str, ClipInfo] = {}
    for path, from_dir in sources:
        lang = clip_language(path.name, args.lang, from_dir)
        if lang is None and is_nonspeech(path.name):
            lang = "te"  # ns_* with no language: the engines still need one; any will do
        if lang is None:
            _log(f"SKIP {path.name}: no te_/hi_/en_ prefix and no --lang")
            continue
        try:
            audio, note = load_audio(path)
        except ValueError as exc:
            _log(f"SKIP {path.name}: {exc}")
            continue
        if note:
            _log(f"NOTE {path.name}: {note}")
        nonspeech = is_nonspeech(path.name)
        ref: str | None = None
        if scoring and not nonspeech:
            if path.name not in refs:
                if not refs_problem:
                    _log(f"NOTE {path.name}: no row in the references file")
            elif refs[path.name][1].strip():
                ref = refs[path.name][1]
            else:
                _log(f"NOTE {path.name}: reference_text is empty")
        clips_by_lang.setdefault(lang, []).append((path.name, audio))
        infos[path.name] = ClipInfo(lang, nonspeech, ref)
    if not clips_by_lang:
        _log("no usable audio files")
        return 1

    def attach_score(r: Result) -> None:
        info = infos[r.file]
        if info.nonspeech:
            r.score = score_pair("", r.transcript, info.lang)
        elif info.ref is None:
            r.score = Score(NO_REF)
        else:
            r.score = score_pair(info.ref, r.transcript, info.lang)

    denoise_fn = get_denoise_fn() if args.denoise else None
    langs = list(clips_by_lang)
    n_clips = sum(len(c) for c in clips_by_lang.values())

    header = [
        f"date: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"CPU: {cpu_model()} ({os.cpu_count()} logical cores)",
        (
            f"{'language' if len(langs) == 1 else 'languages'}: {', '.join(langs)}; "
            f"files: {n_clips}; runs per variant: {args.runs}; "
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
    if args.refs is not None:
        header.append(
            f"references: {args.refs.name} ({refs_problem})"
            if refs_problem
            else f"references: {args.refs.name} ({len(refs)} rows)"
        )

    results: list[Result] = []
    load_lines: list[str] = []

    def collect(r: Result) -> None:
        if scoring:
            attach_score(r)
        results.append(r)

    for lang, clips in clips_by_lang.items():
        tag = f"[{lang}] " if len(langs) > 1 else ""
        lang_wanted = wanted
        if lang == "en":
            lang_wanted = [e for e in wanted if e == "whisper-small"]
            if len(lang_wanted) < len(wanted):
                load_lines.append(f"{tag}IndicConformer has no English: faster-whisper only")
            if not lang_wanted:
                _log("en clips need --engines to include whisper-small; skipped")
        groups: list[tuple[str, Callable[[], list[LoadedEngine]]]] = []
        indic_modes = [m for m in ("ctc", "rnnt") if f"indic-{m}" in lang_wanted]
        if indic_modes:
            groups.append(("indicconformer", lambda m=indic_modes, lg=lang: _load_indic(m, lg)))
        if "whisper-small" in lang_wanted:
            groups.append(
                ("whisper-small", lambda lg=lang: [_load_whisper("small", lg, args.threads)])
            )
        for label, loader in groups:
            _log(f"loading {label} ({lang}) ...")
            try:
                engines = loader()
            except Exception as exc:  # noqa: BLE001 - report it and carry on with the next engine
                msg = f"{tag}{label}: SKIPPED, could not load ({type(exc).__name__}: {exc})"
                _log(msg)
                load_lines.append(msg)
                continue
            for eng in engines:
                load_lines.append(
                    f"{tag}{eng.name}{'/' + eng.mode if eng.mode != '-' else ''}: "
                    f"{eng.model_version}, model load {eng.load_s:.1f}s"
                )
                _log(f"running {eng.name} {eng.mode} ({lang}) ...")
                run_engine(eng, clips, denoise_fn, args.runs, lang, collect)
            del engines

    table = render_markdown(results, header + load_lines, langs[0], args.runs)
    if scoring:
        table += "\n" + render_summary(results)
    print(table)
    if args.out:
        args.out.write_text(table, encoding="utf-8")
        _log(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
