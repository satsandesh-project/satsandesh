"""
One command to bring up the whole live demo:

    services\\ai\\.venv\\Scripts\\python.exe services\\ai\\demo_console\\start_demo.py

Starts, in this order, the real ASR service (IndicConformer by default;
`--asr whisper` for the faster-whisper fallback), the real MT service, the real
render service (English -> target text + audio) and the demo console, each as a
background process logging to demo_console/_logs/. Waits until every one
reports ready, then opens the browser. A service that is already running on
its port is reused, not restarted (its reported model version is printed).
Ctrl+C stops only the processes this script started.

Children run with HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1, so the models
must load from the local Hugging Face cache. HF_TOKEN is passed through from
the environment (IndicConformer and render refuse to start without it) and is
never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_DIR = HERE.parent
REPO_ROOT = AI_DIR.parents[1]
LOG_DIR = HERE / "_logs"

sys.path.insert(0, str(REPO_ROOT))
from services.ai.mt.settings import Settings as MtSettings

# Fixed on purpose: IndicConformer defaults to 8004 and render to 8005, which collide
# with MT and the console. Every child gets these explicitly (see child_env).
ASR_PORT = 8002
RENDER_PORT = int(os.environ.get("DEMO_RENDER_PORT", "8006"))
DEMO_PORT = int(os.environ.get("DEMO_CONSOLE_PORT", "8005"))
# Four processes on CPU: IndicConformer ~7-14 s load plus warm-up, two IndicTrans2 models,
# two Piper voices. Finite, so a stuck service is named instead of waited on forever.
READY_TIMEOUT_S = int(os.environ.get("DEMO_READY_TIMEOUT_S", "900"))


@dataclass
class Service:
    name: str
    module: str  # uvicorn target, relative to services/ai
    port: int
    ready_path: str
    role: str = ""  # "asr" | "mt" | "render" | "console"
    needs_token: bool = False
    extra_env: dict[str, str] = field(default_factory=dict)
    process: subprocess.Popen[bytes] | None = None
    reused: bool = False
    ready: bool = False
    log_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.log_path = LOG_DIR / f"{self.module.split('.')[0]}.log"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _http_status(url: str, timeout: float = 2.0) -> int | None:
    """HTTP status for GET url, or None if nothing answered at all."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _http_json(url: str, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def build_services(asr: str = "indicconformer") -> list[Service]:
    """The services in start order. asr is "indicconformer" (default) or "whisper"."""
    if asr == "whisper":
        asr_svc = Service(
            "ASR (whisper)",
            "speech.app",
            ASR_PORT,
            "/health/ready",
            role="asr",
            extra_env={"ASR_PORT": str(ASR_PORT)},
        )
    elif asr == "indicconformer":
        asr_svc = Service(
            "ASR (conformer)",
            "speech_indicconformer.app",
            ASR_PORT,
            "/health/ready",
            role="asr",
            needs_token=True,
            extra_env={"ASR_PORT": str(ASR_PORT)},
        )
    else:
        raise ValueError(f"unknown ASR choice {asr!r}")
    return [
        asr_svc,
        Service("MT (pivot)", "mt.app", MtSettings.from_env().port, "/health/ready", role="mt"),
        Service(
            "Render",
            "render.app",
            RENDER_PORT,
            "/health/ready",
            role="render",
            needs_token=True,
            extra_env={"RENDER_PORT": str(RENDER_PORT)},
        ),
        Service("Demo console", "demo_console.app", DEMO_PORT, "/health", role="console"),
    ]


def child_env(svc: Service, base: Mapping[str, str]) -> dict[str, str]:
    """Environment for one child. HF_TOKEN passes through from base untouched, if present."""
    env = dict(base)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    # A stale ASR_PORT / RENDER_PORT in the user's shell must not move any child: the
    # console reads the ASR port from the same variable the ASR service listens on.
    env["ASR_PORT"] = str(ASR_PORT)
    env["RENDER_PORT"] = str(RENDER_PORT)
    env["DEMO_RENDER_PORT"] = str(RENDER_PORT)
    env["DEMO_CONSOLE_PORT"] = str(DEMO_PORT)
    env.update(svc.extra_env)
    return env


def asr_identity_warning(model_version: str | None, requested: str) -> str | None:
    """Loud warning when the ASR already running on the port is not the one asked for."""
    if not model_version:
        return None
    is_conformer = "conformer" in model_version.lower()
    is_whisper = "whisper" in model_version.lower()
    if requested == "indicconformer" and not is_conformer:
        found = "faster-whisper" if is_whisper else "an unknown model"
        return (
            f"!! WARNING: IndicConformer was requested but {found} ({model_version}) is already "
            f"running on :{ASR_PORT}. The demo will use THAT one. Stop it and re-run to switch."
        )
    if requested == "whisper" and not is_whisper:
        return (
            f"!! WARNING: faster-whisper was requested but {model_version} is already running "
            f"on :{ASR_PORT}. The demo will use THAT one. Stop it and re-run to switch."
        )
    return None


def missing_token_notice(services: list[Service], env: Mapping[str, str]) -> str | None:
    if env.get("HF_TOKEN", "").strip():
        return None
    names = [
        "render" if s.role == "render" else "IndicConformer ASR" for s in services if s.needs_token
    ]
    if not names:
        return None
    return (
        "!! HF_TOKEN is not set. " + " and ".join(names) + " refuse to start without it. "
        'Set it in this shell first, e.g.  $env:HF_TOKEN = "hf_..."  (it is never printed).'
    )


def _python_exe() -> str:
    venv_python = AI_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = AI_DIR / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _describe_ready(svc: Service, info: dict | None) -> str:
    if info is None:
        return "up, still loading"
    if svc.role == "render":
        voices = ", ".join(f"{k}: {v}" for k, v in (info.get("tts_voices") or {}).items())
        return f"translate: {info.get('mt_model_version')}; voices: {voices}"
    model = info.get("model_version")
    return f"model: {model}" if model else "ready"


def _start(svc: Service, python: str, asr_choice: str) -> None:
    # Something already answering HTTP on this port -> reuse it, don't fight it.
    if (
        _http_status(f"{svc.base_url}{svc.ready_path}") is not None
        or _http_status(f"{svc.base_url}/health/live") is not None
    ):
        svc.reused = True
        info = _http_json(f"{svc.base_url}/health/ready")
        print(
            f"  {svc.name:<15} already running on :{svc.port} - reusing it "
            f"({_describe_ready(svc, info)})"
        )
        if svc.role == "asr" and info is not None:
            warning = asr_identity_warning(info.get("model_version"), asr_choice)
            if warning:
                print("\n  " + warning + "\n")
        return

    env = child_env(svc, os.environ)
    LOG_DIR.mkdir(exist_ok=True)
    log = open(svc.log_path, "wb")  # noqa: SIM115 - handed to the child process
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    svc.process = subprocess.Popen(
        [
            python,
            "-m",
            "uvicorn",
            f"{svc.module}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(svc.port),
        ],
        cwd=AI_DIR,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    try:
        shown_log = svc.log_path.relative_to(REPO_ROOT)
    except ValueError:  # LOG_DIR redirected outside the repo
        shown_log = svc.log_path
    print(f"  {svc.name:<15} starting on :{svc.port} (log: {shown_log})")


def _tail(path: Path, n: int = 15) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no log)"
    return "\n".join("      " + line for line in lines[-n:])


def _last_line(path: Path) -> str:
    try:
        lines = [x.strip() for x in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    except OSError:
        return "(no log)"
    # The last line is often uvicorn's own "Application startup failed"; prefer the cause.
    causes = [x for x in lines if x and not x.startswith(("ERROR:", "INFO:"))]
    return causes[-1] if causes else "(empty log)"


def _wait_ready(services: list[Service], start: float) -> bool:
    last_report = 0.0
    while True:
        for svc in services:
            if svc.ready:
                continue
            if svc.process is not None and svc.process.poll() is not None:
                print(
                    f"\n  !! {svc.name} exited (code {svc.process.returncode}) before becoming "
                    f"ready. Reason: {_last_line(svc.log_path)}\n"
                    f"  Last log lines:\n{_tail(svc.log_path)}\n"
                )
                return False
            if _http_status(f"{svc.base_url}{svc.ready_path}") == 200:
                svc.ready = True
                print(f"  {svc.name:<15} READY   ({time.monotonic() - start:.0f}s)")
        if all(s.ready for s in services):
            return True
        elapsed = time.monotonic() - start
        if elapsed > READY_TIMEOUT_S:
            waiting = ", ".join(s.name for s in services if not s.ready)
            print(
                f"\n  !! Timed out after {READY_TIMEOUT_S}s. NOT READY: {waiting}. "
                "Check the logs in services/ai/demo_console/_logs/ "
                "(set DEMO_READY_TIMEOUT_S to wait longer)."
            )
            return False
        if elapsed - last_report >= 15:
            waiting = ", ".join(s.name for s in services if not s.ready)
            print(f"  ... still loading models ({elapsed:.0f}s): {waiting}")
            last_report = elapsed
        time.sleep(1)


def _stop_started(services: list[Service]) -> None:
    for svc in services:
        if svc.process is not None and svc.process.poll() is None:
            svc.process.terminate()
    for svc in services:
        if svc.process is not None:
            try:
                svc.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                svc.process.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start ASR + MT + render + demo console.")
    parser.add_argument("--no-browser", action="store_true", help="Just print the URL.")
    parser.add_argument(
        "--asr",
        choices=["indicconformer", "whisper"],
        default="indicconformer",
        help="ASR service to run (default: indicconformer; whisper is the fallback).",
    )
    args = parser.parse_args(argv)

    services = build_services(args.asr)
    python = _python_exe()
    print(f"Starting SatSandesh AI demo (python: {python}, ASR: {args.asr})")
    notice = missing_token_notice(services, os.environ)
    if notice:
        print("\n  " + notice + "\n")
    launched_at = time.monotonic()
    for svc in services:
        _start(svc, python, args.asr)

    try:
        if not _wait_ready(services, launched_at):
            print("Demo is NOT ready. Fix the problem above and run this again.")
            _stop_started(services)
            return 1

        ready_s = time.monotonic() - launched_at
        url = f"http://127.0.0.1:{DEMO_PORT}/"
        print("\n" + "=" * 60)
        print(f"  DEMO READY:  {url}   (after {ready_s:.0f}s)")
        print("=" * 60)
        print("Open it via 127.0.0.1 (not a LAN IP) so the browser allows the microphone.")
        print("Leave this window open. Press Ctrl+C here to stop the services it started.\n")
        if not args.no_browser:
            webbrowser.open(url)

        while True:
            time.sleep(2)
            for svc in services:
                if svc.process is not None and svc.process.poll() is not None and svc.ready:
                    svc.ready = False
                    print(
                        f"  !! {svc.name} stopped (code {svc.process.returncode}). "
                        "Re-run start_demo.py to bring it back; the others keep running."
                    )
    except KeyboardInterrupt:
        print("\nStopping services started by this script...")
    finally:
        _stop_started(services)
    return 0


if __name__ == "__main__":
    sys.exit(main())
