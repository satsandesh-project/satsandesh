"""
Latency report for the real AI services, measured over HTTP the way the gateway calls them.

Evidence-gathering tooling only: not imported by any service, changes no service, no routing
and no contract. It times what is already running; it tunes nothing. Whatever numbers come
out are the numbers that get published.

Per recording it runs one chain per ASR variant, `--runs` times:

    ASR  (POST /v1/transcribe)  ->  MT pivot (POST /v1/pivot)  ->  render (POST /v1/render)
                                                              [->  moderate (POST /v1/moderate)]

  te / hi : IndicConformer (CTC and/or RNNT, see --indic-decoder) is the primary ASR;
            faster-whisper runs on the same clips as a labelled comparison column.
  en      : faster-whisper. The MT pivot is a passthrough for English, so it is recorded as
            "passthrough" and is not timed.

The pivot runs on that ASR variant's own transcript; render asks for the other two languages
of {en, hi, te} in one request (a circle message fans out in one call). Moderation is only
run with --with-moderation (it needs the Qwen model).

Cold vs warm: the first request this tool sends to each service URL is "cold"; every later
request to that URL is warm, whichever language it carries. p50/p90 are over WARM, SUCCESSFUL
calls only and are printed ONLY when --runs >= 5 (as in asr_compare.py). A failed or timed-out
call is recorded as a failure with its elapsed time; it is never dropped and never aborts the
run.

Audio is sent as a file:// URI (the contract's AudioRef), so the services must run on the
same machine as this tool. Recordings: 16 kHz mono 16-bit PCM WAV (or .opus/.ogg/.mp3, which
the services decode with ffmpeg). Never commit them. The report names no files and contains no
transcripts: only counts, durations and timings.

Clip languages, in this order: `--audio-dir te=DIR` (explicit), a sub-folder named te/hi/en,
a filename starting `te_` / `te-` / `te.`, then `--lang`.

    cd services/ai
    $env:PYTHONPATH = "..\\.."
    .\\.venv\\Scripts\\python.exe tools/latency_report.py --audio-dir C:\\rec --runs 5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
import urllib.error
import urllib.request
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.ai.tools.asr_compare import PERCENTILE_MIN_RUNS, cpu_model

CONTRACT_VERSION = "0.1.0"
LANGS = ("en", "hi", "te")
TARGET_P90_S = 10.0
TARGET_NOTE_S = 30.0

# 8004 is the documented default of BOTH the MT service and the IndicConformer service, so
# they cannot both default to it. MT keeps its README port; the two IndicConformer
# instances (one per decoder; DECODE_MODE is a per-process setting) get free ports.
DEFAULT_URLS = {
    "whisper": "http://127.0.0.1:8002",
    "indic-ctc": "http://127.0.0.1:8006",
    "indic-rnnt": "http://127.0.0.1:8007",
    "mt": "http://127.0.0.1:8004",
    "render": "http://127.0.0.1:8005",
    "moderation": "http://127.0.0.1:8003",
}
URL_ENV = {
    "whisper": "LATENCY_ASR_URL",
    "indic-ctc": "LATENCY_INDIC_CTC_URL",
    "indic-rnnt": "LATENCY_INDIC_RNNT_URL",
    "mt": "LATENCY_MT_URL",
    "render": "LATENCY_RENDER_URL",
    "moderation": "LATENCY_MODERATION_URL",
}
AUDIO_FORMATS = {".wav": "wav_pcm16", ".opus": "ogg_opus", ".ogg": "ogg_opus", ".mp3": "mp3"}

# Settings that change speed. Never anything secret: HF_TOKEN is deliberately not listed.
ENV_VARS = (
    "ASR_CPU_THREADS",
    "ASR_MODEL_NAME",
    "ASR_COMPUTE_TYPE",
    "ASR_VAD_FILTER",
    "ASR_HINT_MODE",
    "ASR_DENOISE",
    "ASR_NO_SPEECH_GUARD",
    "DECODE_MODE",
    "MT_DEVICE",
    "MT_NUM_BEAMS",
    "RENDER_MT_DEVICE",
    "RENDER_NUM_BEAMS",
    "MOD_BACKEND",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "CT2_INTER_THREADS",
    "CUDA_VISIBLE_DEVICES",
)

# ---- maths ---------------------------------------------------------------------------


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default), q in [0, 100]."""
    if not values:
        raise ValueError("percentile of no values")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def median(values: list[float]) -> float:
    return percentile(values, 50)


# ---- HTTP ----------------------------------------------------------------------------


@dataclass
class HttpResult:
    ok: bool
    elapsed_s: float
    status: int | None = None
    body: dict[str, Any] | None = None
    error: str | None = None


Http = Callable[[str, str, dict[str, Any] | None, float], HttpResult]


def http_call(
    method: str, url: str, payload: dict[str, Any] | None, timeout_s: float
) -> HttpResult:
    """One request, timed with a monotonic client-side clock. Never raises: every failure
    comes back as ok=False with the elapsed time and a short, content-free reason."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    start = time.perf_counter()
    status: int | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status = resp.status
            raw = resp.read()
        body = json.loads(raw)
        return HttpResult(True, time.perf_counter() - start, status, body)
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - start
        reason = f"HTTP {exc.code}"
        try:
            err = json.loads(exc.read())
            reason += f" {err.get('code', '')} {err.get('stage', '')}".rstrip()
        except (ValueError, OSError):
            pass
        return HttpResult(False, elapsed, exc.code, None, reason)
    except TimeoutError:
        return HttpResult(False, time.perf_counter() - start, None, None, "timeout")
    except (urllib.error.URLError, OSError) as exc:
        cause: object = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        label = "timeout" if isinstance(cause, TimeoutError) else type(cause).__name__
        return HttpResult(False, time.perf_counter() - start, None, None, label)
    except ValueError:
        return HttpResult(False, time.perf_counter() - start, status, None, "invalid JSON reply")


# ---- clips ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Clip:
    label: str  # "te-1": a counter, never the file name
    lang: str
    path: Path
    duration_s: float | None
    audio_format: str

    @property
    def uri(self) -> str:
        return self.path.resolve().as_uri()


def wav_duration_s(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except (wave.Error, OSError, EOFError, ZeroDivisionError):
        return None


def _lang_from_name(name: str) -> str | None:
    for lang in LANGS:
        if name.lower().startswith((f"{lang}_", f"{lang}-", f"{lang}.")):
            return lang
    return None


def discover_clips(specs: list[str], default_lang: str | None) -> tuple[list[Clip], list[str]]:
    """(clips, notes). Each spec is `DIR` or `LANG=DIR`. Skipped files are noted by count."""
    found: list[tuple[str, Path]] = []
    notes: list[str] = []
    skipped_unknown_lang = 0
    for spec in specs:
        forced: str | None = None
        head, sep, tail = spec.partition("=")
        if sep and head in LANGS:
            forced, spec = head, tail
        root = Path(spec)
        if not root.is_dir():
            notes.append("an --audio-dir folder was not found")
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_FORMATS:
                continue
            rel_parts = [p.lower() for p in path.relative_to(root).parts[:-1]]
            lang = (
                forced
                or next((p for p in rel_parts if p in LANGS), None)
                or _lang_from_name(path.name)
                or default_lang
            )
            if lang is None:
                skipped_unknown_lang += 1
                continue
            found.append((lang, path))
    if skipped_unknown_lang:
        notes.append(f"{skipped_unknown_lang} file(s) skipped: language could not be determined")
    counters = dict.fromkeys(LANGS, 0)
    clips = []
    for lang, path in found:
        counters[lang] += 1
        fmt = AUDIO_FORMATS[path.suffix.lower()]
        dur = wav_duration_s(path) if fmt == "wav_pcm16" else None
        clips.append(Clip(f"{lang}-{counters[lang]}", lang, path, dur, fmt))
    return clips, notes


# ---- measurement records ---------------------------------------------------------------


@dataclass
class Call:
    lang: str
    chain: str
    stage: str  # asr | pivot | render | moderate
    clip: str
    run: int
    ok: bool
    elapsed_s: float
    cold: bool
    audio_s: float | None = None
    reported_ms: float | None = None  # the service's own duration_ms
    timings_ms: dict[str, float] = field(default_factory=dict)  # stage_timings, if returned
    error: str | None = None
    empty: bool = False  # ASR returned an empty transcript
    degraded: int = 0  # render results that came back degraded


@dataclass
class ChainRun:
    lang: str
    chain: str
    clip: str
    run: int
    ok: bool
    total_s: float | None  # sum of the timed stages; None when any stage failed
    cold: bool  # at least one stage was a cold call


@dataclass
class Skipped:
    lang: str
    chain: str
    stage: str
    count: int = 0


@dataclass
class Chain:
    name: str  # label used in tables
    url_key: str
    comparison: bool = False  # faster-whisper on te/hi


def chains_for(lang: str, indic: str, compare_whisper: bool) -> list[Chain]:
    if lang == "en":
        return [Chain("faster-whisper", "whisper")]
    out: list[Chain] = []
    if indic in ("ctc", "both"):
        out.append(Chain("IndicConformer CTC", "indic-ctc"))
    if indic in ("rnnt", "both"):
        out.append(Chain("IndicConformer RNNT", "indic-rnnt"))
    if compare_whisper:
        out.append(Chain("faster-whisper (comparison)", "whisper", comparison=True))
    return out


def other_languages(lang: str) -> list[str]:
    return [x for x in LANGS if x != lang]


class Runner:
    def __init__(self, urls: dict[str, str], timeout_s: float, http: Http, moderation: bool):
        self.urls = urls
        self.timeout_s = timeout_s
        self.http = http
        self.moderation = moderation
        self.calls: list[Call] = []
        self.chain_runs: list[ChainRun] = []
        self.skipped: dict[tuple[str, str, str], Skipped] = {}
        self._seen: set[str] = set()

    def _post(self, url_key: str, path: str, payload: dict[str, Any]) -> tuple[HttpResult, bool]:
        base = self.urls[url_key]
        cold = base not in self._seen
        self._seen.add(base)
        return self.http("POST", f"{base}{path}", payload, self.timeout_s), cold

    def _skip(self, lang: str, chain: str, stage: str) -> None:
        key = (lang, chain, stage)
        self.skipped.setdefault(key, Skipped(lang, chain, stage)).count += 1

    def run_chain(self, clip: Clip, chain: Chain, run: int) -> None:
        lang, name = clip.lang, chain.name
        common: dict[str, Any] = {"lang": lang, "chain": name, "clip": clip.label, "run": run}
        audio: dict[str, Any] = {"uri": clip.uri, "format": clip.audio_format}
        if clip.duration_s is not None:
            audio["duration_ms"] = round(clip.duration_s * 1000)
        res, cold = self._post(
            chain.url_key,
            "/v1/transcribe",
            {"contract_version": CONTRACT_VERSION, "audio": audio, "language_hint": lang},
        )
        body = res.body or {}
        asr = Call(
            stage="asr",
            ok=res.ok,
            elapsed_s=res.elapsed_s,
            cold=cold,
            audio_s=clip.duration_s,
            reported_ms=body.get("duration_ms"),
            error=res.error,
            timings_ms={t["stage"]: t["duration_ms"] for t in body.get("stage_timings", [])},
            empty=res.ok and not str(body.get("text", "")).strip(),
            **common,
        )
        self.calls.append(asr)
        stages = [asr]
        text = str(body.get("text", "")) if res.ok else ""
        pivot_text: str | None = None

        if not res.ok:
            self._skip(lang, name, "pivot")
        elif lang == "en":
            pivot_text = text  # passthrough: recorded in the report, not timed
        else:
            pres, pcold = self._post(
                "mt",
                "/v1/pivot",
                {"contract_version": CONTRACT_VERSION, "text": text, "source_language": lang},
            )
            pbody = pres.body or {}
            pivot = Call(
                stage="pivot",
                ok=pres.ok,
                elapsed_s=pres.elapsed_s,
                cold=pcold,
                reported_ms=pbody.get("duration_ms"),
                error=pres.error,
                **common,
            )
            self.calls.append(pivot)
            stages.append(pivot)
            if pres.ok:
                pivot_text = str(pbody.get("pivot_text", ""))

        if pivot_text is None:
            self._skip(lang, name, "render")
        else:
            rres, rcold = self._post(
                "render",
                "/v1/render",
                {
                    "contract_version": CONTRACT_VERSION,
                    "pivot_text": pivot_text,
                    "target_languages": other_languages(lang),
                },
            )
            results = (rres.body or {}).get("results", [])
            render = Call(
                stage="render",
                ok=rres.ok,
                elapsed_s=rres.elapsed_s,
                cold=rcold,
                reported_ms=sum(r.get("duration_ms", 0.0) for r in results) if results else None,
                timings_ms={
                    f"render.{r.get('language')}": r.get("duration_ms", 0.0) for r in results
                },
                error=rres.error,
                degraded=sum(1 for r in results if r.get("degraded", {}).get("active")),
                **common,
            )
            self.calls.append(render)
            stages.append(render)
            if self.moderation:
                mres, mcold = self._post(
                    "moderation",
                    "/v1/moderate",
                    {"contract_version": CONTRACT_VERSION, "text": pivot_text},
                )
                mod = Call(
                    stage="moderate",
                    ok=mres.ok,
                    elapsed_s=mres.elapsed_s,
                    cold=mcold,
                    error=mres.error,
                    **common,
                )
                self.calls.append(mod)
                stages.append(mod)

        # en has no timed pivot stage: asr + render (+ moderate); the others add the pivot.
        expected = (3 if lang != "en" else 2) + (1 if self.moderation else 0)
        all_ok = len(stages) == expected and all(s.ok for s in stages)
        self.chain_runs.append(
            ChainRun(
                lang,
                name,
                clip.label,
                run,
                all_ok,
                sum(s.elapsed_s for s in stages) if all_ok else None,
                any(s.cold for s in stages),
            )
        )

    def run_all(
        self,
        clips: list[Clip],
        runs: int,
        indic: str,
        compare_whisper: bool,
        log: Callable[[str], None] = lambda _m: None,
    ) -> None:
        # Run-major: every clip is visited once per round, so a slow drift (thermal
        # throttling, other load) spreads over all clips instead of landing on the last one.
        for run in range(runs):
            for clip in clips:
                for chain in chains_for(clip.lang, indic, compare_whisper):
                    log(f"run {run + 1}/{runs} {clip.label} {chain.name}")
                    self.run_chain(clip, chain, run)


# ---- aggregation -----------------------------------------------------------------------


@dataclass
class Summary:
    n_ok: int
    n_fail: int
    cold: float | None
    warm: list[float]
    reported_ms_median: float | None
    rtf_median: float | None
    timings_ms_median: dict[str, float]
    fail_times: list[float]
    errors: dict[str, int]
    empty: int
    degraded: int

    def pct(self, runs: int) -> tuple[float, float] | None:
        """(p50, p90), only for --runs >= 5 and at least one warm sample."""
        if runs < PERCENTILE_MIN_RUNS or not self.warm:
            return None
        return median(self.warm), percentile(self.warm, 90)


def summarize(calls: list[Call]) -> Summary:
    ok = [c for c in calls if c.ok]
    warm = [c for c in ok if not c.cold]
    cold = next((c.elapsed_s for c in ok if c.cold), None)
    rep = [c.reported_ms for c in warm if c.reported_ms is not None]
    rtf = [c.elapsed_s / c.audio_s for c in warm if c.audio_s]
    keys = sorted({k for c in warm for k in c.timings_ms})
    errors: dict[str, int] = {}
    for c in calls:
        if not c.ok:
            errors[c.error or "unknown"] = errors.get(c.error or "unknown", 0) + 1
    return Summary(
        n_ok=len(ok),
        n_fail=len(calls) - len(ok),
        cold=cold,
        warm=[c.elapsed_s for c in warm],
        reported_ms_median=median(rep) if rep else None,
        rtf_median=median(rtf) if rtf else None,
        timings_ms_median={
            k: median([c.timings_ms[k] for c in warm if k in c.timings_ms]) for k in keys
        },
        fail_times=[c.elapsed_s for c in calls if not c.ok],
        errors=errors,
        empty=sum(1 for c in ok if c.empty),
        degraded=sum(c.degraded for c in ok),
    )


# ---- environment -----------------------------------------------------------------------


def total_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemStatus()
            status.dwLength = ctypes.sizeof(MemStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return status.ullTotalPhys / 1024**3
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024**2
    except (OSError, ValueError, AttributeError):
        pass
    return None


def machine_specs() -> dict[str, str]:
    ram = total_ram_gb()
    env = {k: os.environ[k] for k in ENV_VARS if k in os.environ}
    return {
        "CPU": cpu_model(),
        "Logical cores": str(os.cpu_count()),
        "RAM": f"{ram:.1f} GB" if ram is not None else "unknown",
        "OS": platform.platform(),
        "Python": platform.python_version(),
        "Thread / speed env vars (this shell)": (
            ", ".join(f"`{k}={v}`" for k, v in env.items()) if env else "none set"
        ),
    }


def probe_services(urls: dict[str, str], used: list[str], http: Http) -> dict[str, str]:
    """GET /health/ready for each service this run uses. Never raises. A decode_mode that
    does not match the instance's slot is reported, not corrected."""
    out: dict[str, str] = {}
    for key in used:
        res = http("GET", f"{urls[key]}/health/ready", None, 5.0)
        if not res.ok or not res.body:
            out[key] = f"not ready or unreachable ({res.error or 'no body'})"
            continue
        body = res.body
        bits = [str(body.get("model_version", "model version not reported"))]
        mode = body.get("decode_mode")
        if mode:
            bits.append(f"decode_mode={mode}")
            want = key.removeprefix("indic-")
            if key.startswith("indic-") and mode != want:
                bits.append(f"**WARNING: this slot expects {want}**")
        out[key] = ", ".join(bits)
    return out


# ---- markdown --------------------------------------------------------------------------


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _s(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _table(cols: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return out + [""]


def _group(calls: list[Call]) -> dict[tuple[str, str, str], list[Call]]:
    groups: dict[tuple[str, str, str], list[Call]] = {}
    for c in calls:
        groups.setdefault((c.lang, c.chain, c.stage), []).append(c)
    return groups


def _durations(clips: list[Clip]) -> str:
    known = [c.duration_s for c in clips if c.duration_s is not None]
    if not known:
        return "duration unknown"
    return f"{min(known):.1f}-{max(known):.1f} s each, {sum(known):.0f} s in total"


def verdict(chain_runs: list[ChainRun], clips: list[Clip], runs: int) -> tuple[list[str], str]:
    """(per-lang/chain lines, plain sentence). Warm, fully successful runs only."""
    lines: list[str] = []
    met: list[str] = []
    missed: list[str] = []
    unknown: list[str] = []
    keys = sorted({(r.lang, r.chain) for r in chain_runs})
    for lang, chain in keys:
        label = f"{lang} / {chain}"
        warm = [
            r.total_s
            for r in chain_runs
            if r.lang == lang and r.chain == chain and r.ok and not r.cold and r.total_s is not None
        ]
        if runs < PERCENTILE_MIN_RUNS or not warm:
            unknown.append(label)
            reason = (
                f"needs --runs >= {PERCENTILE_MIN_RUNS}"
                if runs < PERCENTILE_MIN_RUNS
                else "no successful warm run"
            )
            lines.append(f"- {label}: NOT EVALUATED ({reason}).")
            continue
        p90 = percentile(warm, 90)
        ok = p90 < TARGET_P90_S
        (met if ok else missed).append(label)
        lines.append(
            f"- {label}: end-to-end p90 **{p90:.2f} s** over {len(warm)} warm run(s) "
            f"-> **{'MET' if ok else 'MISSED'}** (target < {TARGET_P90_S:g} s)."
        )
    parts = []
    if met:
        parts.append(f"met for {', '.join(met)}")
    if missed:
        parts.append(f"missed for {', '.join(missed)}")
    if unknown:
        parts.append(f"not evaluated for {', '.join(unknown)}")
    sentence = "Target " + "; ".join(parts) + "." if parts else "Target: nothing was measured."
    return lines, sentence


CONTEXT = """\
These are the numbers already on `main`, in `infra/ai/GATE_WEEK8.md` ("The p90" and its
2026-10-08 follow-up). They are here for orientation only.

| Earlier measurement | Value |
|---|---|
| faster-whisper, Hindi 30 s note, whole pipeline, n=8 | p90 29.4 s |
| faster-whisper, Telugu 30 s note, whole pipeline, n=8 | p90 119.9 s |
| faster-whisper, ASR call alone, 33.5 s Telugu note, n=8 | p50 79.9 s, worst 130.3 s |

**Those used synthetic speech, on a different machine** (a shared 16-CPU server with a load
average of about 3-5 from other stacks, `ASR_CPU_THREADS=4`). This report uses real recordings
on the machine described above, so the comparison is **indicative only**: it is not a
like-for-like benchmark and neither set of numbers proves the other wrong.
"""

NOT_SHOWN = """\
- **One speaker.** All clips are one person's voice. Nothing here says how other voices, accents
  or ages behave, and real elder voices were not tested.
- **One machine.** The numbers are for the machine above, with the thread settings above. A
  different CPU, a limited container or a GPU will give different numbers.
- **No concurrency.** One request at a time. The services queue, so with several notes at once
  the wait is longer (the 10th of ten simultaneous notes waits about ten times a single note).
- **No gateway.** Client-side timing of the services' own HTTP APIs, with no gateway, queue,
  retry or network hop. This is not the production path's end-to-end time.
- **No accuracy claim.** Transcripts are not stored, read or scored. A fast wrong transcript
  looks the same as a fast right one here.
- **Warm means "after the tool's first call to that URL".** It does not mean steady state under
  load, and a service that was already used before the run has no cold row at all.
"""


def render_markdown(
    runner: Runner,
    clips: list[Clip],
    runs: int,
    specs: dict[str, str],
    health: dict[str, str],
    urls: dict[str, str],
    indic: str,
    compare_whisper: bool,
    notes: list[str],
    moderation: bool,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now().astimezone()
    show_pct = runs >= PERCENTILE_MIN_RUNS
    out = ["# AI latency report", ""]
    out += [
        f"Generated {now.isoformat(timespec='seconds')} by `services/ai/tools/latency_report.py`.",
        "",
    ]

    out += ["## Machine", ""]
    out += _table(["", ""], [[k, v] for k, v in specs.items()])
    out += [
        (
            "The thread settings are those of the shell that ran this tool. The services were "
            "started separately; they only share them if they were started from the same shell."
        ),
        "",
    ]

    by_lang = {lang: [c for c in clips if c.lang == lang] for lang in LANGS}
    out += ["## Method", ""]
    out += [
        "Client-side timing of the real services over HTTP, one request at a time, the way the "
        "gateway calls them: ASR (`/v1/transcribe`), then the MT pivot (`/v1/pivot`) on that "
        "ASR's own transcript, then render (`/v1/render`) for the other two languages of "
        "{en, hi, te} in one request."
        + (
            " Moderation (`/v1/moderate`) is included."
            if moderation
            else " Moderation was not run."
        ),
        "",
        f"- Runs: {runs} per clip and chain, interleaved across clips.",
        "- Clips (counts and durations only; no file names, no transcripts):",
    ]
    for lang in LANGS:
        group = by_lang[lang]
        if group:
            out.append(f"  - {lang}: {len(group)} clip(s), {_durations(group)}")
    out += [
        f"- ASR for te/hi: IndicConformer, decoder `{indic}`; faster-whisper on the same clips "
        + (
            "as a labelled comparison."
            if compare_whisper
            else "was skipped (`--skip-whisper-compare`)."
        ),
        "- English: faster-whisper; the pivot is a passthrough (not timed), then render.",
        (
            "- **Cold** = the first request this tool sent to that service URL. **Warm** = every "
            "later request to it, whichever language it carried, so a service already warmed by "
            "another language has no cold row. p50/p90 are over warm, successful calls only."
        ),
        (
            "- Elapsed time is measured by this tool's own clock around the HTTP request. "
            "Failures and timeouts are listed with their elapsed time and are not in p50/p90."
        ),
        (
            "- Render of the `en` target is text-only by design (there is no English voice), so a "
            "degraded flag on it is expected."
        ),
        "- The end-to-end total is the sum of the timed stages for one chain run.",
        "",
        "Services used:",
        "",
    ]
    for key, text in health.items():
        out.append(f"- `{key}` at `{urls[key]}`: {text}")
    out.append("")
    if not show_pct:
        out += [
            (
                f"**p50/p90 are omitted: `--runs {runs}` is below {PERCENTILE_MIN_RUNS}.** "
                "Individual warm runs are shown instead."
            ),
            "",
        ]
    else:
        out += [
            (
                "**Percentile caveat:** with few samples p90 is close to the worst value. With "
                "n=5 it is interpolated between the two largest values (60% of the way to the "
                "worst); read it as 'about the worst case seen', not as a stable tail estimate. Each table shows its n."
            ),
            "",
        ]
    for n in notes:
        out.append(f"- Note: {n}")
    if notes:
        out.append("")

    groups = _group(runner.calls)
    out += ["## Results by language and stage", ""]
    for lang in LANGS:
        if not by_lang[lang]:
            continue
        out += [f"### {lang}", ""]
        cols = ["chain", "stage", "ok / failed", "cold (s)", "warm n"]
        cols += ["p50 (s)", "p90 (s)"] if show_pct else ["warm runs (s)"]
        cols += ["max (s)", "service-reported (s)", "RTF"]
        rows: list[list[object]] = []
        for chain in chains_for(lang, indic, compare_whisper):
            for stage in ("asr", "pivot", "render", "moderate"):
                calls = groups.get((lang, chain.name, stage))
                if not calls:
                    if stage == "pivot" and lang == "en":
                        rows.append([chain.name, "pivot", "passthrough", *["-"] * (len(cols) - 3)])
                    continue
                sm = summarize(calls)
                row: list[object] = [
                    chain.name,
                    stage,
                    f"{sm.n_ok} / {sm.n_fail}",
                    _s(sm.cold),
                    len(sm.warm),
                ]
                pct = sm.pct(runs)
                if show_pct:
                    row += [_s(pct[0]), _s(pct[1])] if pct else ["-", "-"]
                else:
                    row.append(" / ".join(f"{t:.2f}" for t in sm.warm) or "-")
                row += [
                    _s(max(sm.warm)) if sm.warm else "-",
                    _s(sm.reported_ms_median / 1000) if sm.reported_ms_median is not None else "-",
                    _s(sm.rtf_median) if stage == "asr" else "-",
                ]
                rows.append(row)
        out += _table(cols, rows)
        out += [
            (
                "`service-reported` is the median `duration_ms` the service returned for warm calls "
                "(for render, the sum over the target languages). `RTF` = elapsed / audio duration, "
                "median over warm calls."
            ),
            "",
        ]
        timing_rows: list[list[object]] = []
        for chain in chains_for(lang, indic, compare_whisper):
            for stage in ("asr", "render"):
                calls = groups.get((lang, chain.name, stage))
                if calls:
                    for k, v in summarize(calls).timings_ms_median.items():
                        timing_rows.append([chain.name, stage, k, f"{v / 1000:.3f}"])
        if timing_rows:
            out += ["Service-reported `stage_timings` (median over warm calls):", ""]
            out += _table(["chain", "stage", "reported stage", "median (s)"], timing_rows)

    out += ["## End-to-end total per clip", ""]
    out += [
        (
            "Sum of the timed stages for one run of the chain. A run counts only if every stage "
            "succeeded; 'cold' runs (any stage was a first call) are listed apart from warm ones."
        ),
        "",
    ]
    cols = ["lang", "chain", "clip", "audio (s)", "ok / failed runs", "cold run (s)", "warm n"]
    cols += ["p50 (s)", "p90 (s)"] if show_pct else ["warm runs (s)"]
    rows = []
    clip_by_label = {c.label: c for c in clips}
    keys = sorted(
        {(r.lang, r.chain, r.clip) for r in runner.chain_runs}, key=lambda k: (k[0], k[1], k[2])
    )
    for lang, chain_name, label in keys:
        runs_here = [
            r for r in runner.chain_runs if (r.lang, r.chain, r.clip) == (lang, chain_name, label)
        ]
        rows.append(
            _e2e_row(
                lang, chain_name, label, clip_by_label[label].duration_s, runs_here, runs, show_pct
            )
        )
    for lang, chain_name in sorted({(r.lang, r.chain) for r in runner.chain_runs}):
        runs_here = [r for r in runner.chain_runs if (r.lang, r.chain) == (lang, chain_name)]
        rows.append(_e2e_row(lang, chain_name, "**all clips**", None, runs_here, runs, show_pct))
    out += _table(cols, rows)

    failures = [c for c in runner.calls if not c.ok]
    skipped = list(runner.skipped.values())
    if failures or skipped:
        out += ["## Failures", ""]
        if failures:
            counts: dict[tuple[str, str, str, str], list[float]] = {}
            for c in failures:
                counts.setdefault((c.lang, c.chain, c.stage, c.error or "unknown"), []).append(
                    c.elapsed_s
                )
            out += _table(
                ["lang", "chain", "stage", "error", "count", "elapsed (s)"],
                [
                    [*k, len(v), " / ".join(f"{t:.2f}" for t in v)]
                    for k, v in sorted(counts.items())
                ],
            )
        for s in skipped:
            out.append(
                f"- {s.lang} / {s.chain}: {s.count} run(s) did not reach the {s.stage} stage "
                "because an earlier stage failed."
            )
        out.append("")
    empties = sum(1 for c in runner.calls if c.stage == "asr" and c.ok and c.empty)
    if empties:
        out += [
            (
                f"{empties} ASR call(s) returned an empty transcript (counted as successes; the "
                "later stages then ran on empty text and will look unrealistically fast)."
            ),
            "",
        ]
    degraded = sum(c.degraded for c in runner.calls if c.stage == "render")
    if degraded:
        out += [f"{degraded} render result(s) came back with `degraded.active` set.", ""]

    out += ["## Against the proposal target", ""]
    out += [
        (
            f"The target is end-to-end p90 under {TARGET_P90_S:g} s for a {TARGET_NOTE_S:g} s note. "
            "Measured here as the p90 of warm, fully successful chain runs, per language and ASR "
            "chain, for the clips described under Method (their actual durations are listed there; "
            "they are not guaranteed to be 30 s)."
        ),
        "",
    ]
    lines, sentence = verdict(runner.chain_runs, clips, runs)
    out += lines + ["", f"**{sentence}**", ""]

    out += ["## Context: earlier numbers on `main` (indicative only)", "", CONTEXT]
    out += ["## What this does not show", "", NOT_SHOWN]
    return "\n".join(out)


def _e2e_row(
    lang: str,
    chain: str,
    label: str,
    audio_s: float | None,
    runs_here: list[ChainRun],
    runs: int,
    show_pct: bool,
) -> list[object]:
    ok = [r for r in runs_here if r.ok and r.total_s is not None]
    warm = [r.total_s for r in ok if not r.cold and r.total_s is not None]
    cold = [r.total_s for r in ok if r.cold and r.total_s is not None]
    row: list[object] = [
        lang,
        chain,
        label,
        _s(audio_s),
        f"{len(ok)} / {len(runs_here) - len(ok)}",
        " / ".join(f"{t:.2f}" for t in cold) or "-",
        len(warm),
    ]
    if show_pct:
        row += [_s(median(warm)), _s(percentile(warm, 90))] if warm else ["-", "-"]
    else:
        row.append(" / ".join(f"{t:.2f}" for t in warm) or "-")
    return row


# ---- main ------------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--audio-dir",
        action="append",
        required=True,
        metavar="[LANG=]DIR",
        help="folder of recordings (repeatable); LANG= forces the language of that folder",
    )
    p.add_argument("--lang", choices=LANGS, help="language for files whose language is unclear")
    p.add_argument("--runs", type=int, default=5, help="runs per clip and chain (default 5)")
    p.add_argument("--indic-decoder", choices=["ctc", "rnnt", "both"], default="both")
    p.add_argument(
        "--skip-whisper-compare",
        action="store_true",
        help="do not run faster-whisper on te/hi clips (it is slow on Telugu)",
    )
    p.add_argument("--with-moderation", action="store_true", help="also time /v1/moderate")
    p.add_argument("--timeout", type=float, default=300.0, help="per-request timeout, seconds")
    p.add_argument("--out", type=Path, default=Path("docs/AI_LATENCY_REPORT.md"))
    for key, default in DEFAULT_URLS.items():
        p.add_argument(
            f"--{key}-url" if key != "whisper" else "--asr-url",
            dest=f"url_{key.replace('-', '_')}",
            default=None,
            help=f"default: ${URL_ENV[key]} or {default}",
        )
    return p


def resolve_urls(args: argparse.Namespace) -> dict[str, str]:
    urls = {}
    for key, default in DEFAULT_URLS.items():
        flag = getattr(args, f"url_{key.replace('-', '_')}")
        urls[key] = (flag or os.environ.get(URL_ENV[key]) or default).rstrip("/")
    return urls


def main(argv: list[str] | None = None, http: Http = http_call) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be >= 1")
    compare = not args.skip_whisper_compare

    clips, notes = discover_clips(args.audio_dir, args.lang)
    if not clips:
        _log("no usable audio files found")
        return 1
    urls = resolve_urls(args)

    used: set[str] = set()
    for clip in clips:
        used.update(c.url_key for c in chains_for(clip.lang, args.indic_decoder, compare))
        used.add("render")
        if clip.lang != "en":
            used.add("mt")
    if args.with_moderation:
        used.add("moderation")
    used_sorted = [k for k in DEFAULT_URLS if k in used]
    health = probe_services(urls, used_sorted, http)
    for key, text in health.items():
        _log(f"{key}: {text}")

    runner = Runner(urls, args.timeout, http, args.with_moderation)
    runner.run_all(clips, args.runs, args.indic_decoder, compare, _log)

    report = render_markdown(
        runner,
        clips,
        args.runs,
        machine_specs(),
        health,
        urls,
        args.indic_decoder,
        compare,
        notes,
        args.with_moderation,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    _log(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
