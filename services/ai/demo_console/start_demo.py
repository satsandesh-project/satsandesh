"""
One command to bring up the whole live demo:

    services\\ai\\.venv\\Scripts\\python.exe services\\ai\\demo_console\\start_demo.py

Starts the real ASR service, the real MT service, and the demo console (each
as a background process logging to demo_console/_logs/), waits until every one
reports ready, then opens the browser. A service that is already running on
its port is reused, not restarted. Ctrl+C stops only the processes this script
started.

Children run with HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1, so the models
must load from the local Hugging Face cache — if they come up ready here, the
demo has no network dependency.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_DIR = HERE.parent
REPO_ROOT = AI_DIR.parents[1]
LOG_DIR = HERE / "_logs"

sys.path.insert(0, str(REPO_ROOT))
from services.ai.mt.settings import Settings as MtSettings
from services.ai.speech.settings import Settings as SpeechSettings

DEMO_PORT = int(os.environ.get("DEMO_CONSOLE_PORT", "8005"))
READY_TIMEOUT_S = 600  # cold model load on CPU can take a couple of minutes


@dataclass
class Service:
    name: str
    module: str  # uvicorn target, relative to services/ai
    port: int
    ready_path: str
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


def _python_exe() -> str:
    venv_python = AI_DIR / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = AI_DIR / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _start(svc: Service, python: str) -> None:
    # Something already answering HTTP on this port -> reuse it, don't fight it.
    if (
        _http_status(f"{svc.base_url}{svc.ready_path}") is not None
        or _http_status(f"{svc.base_url}/health/live") is not None
    ):
        svc.reused = True
        print(f"  {svc.name:<13} already running on :{svc.port} - reusing it")
        return

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    LOG_DIR.mkdir(exist_ok=True)
    log = open(svc.log_path, "wb")  # noqa: SIM115 — handed to the child process
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
    print(f"  {svc.name:<13} starting on :{svc.port} (log: {svc.log_path.relative_to(REPO_ROOT)})")


def _tail(path: Path, n: int = 15) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no log)"
    return "\n".join("      " + line for line in lines[-n:])


def _wait_ready(services: list[Service], start: float) -> bool:
    last_report = 0.0
    while True:
        for svc in services:
            if svc.ready:
                continue
            if svc.process is not None and svc.process.poll() is not None:
                print(
                    f"\n  !! {svc.name} exited (code {svc.process.returncode}) before becoming "
                    f"ready. Last log lines:\n{_tail(svc.log_path)}\n"
                )
                return False
            if _http_status(f"{svc.base_url}{svc.ready_path}") == 200:
                svc.ready = True
                print(f"  {svc.name:<13} READY   ({time.monotonic() - start:.0f}s)")
        if all(s.ready for s in services):
            return True
        elapsed = time.monotonic() - start
        if elapsed > READY_TIMEOUT_S:
            waiting = ", ".join(s.name for s in services if not s.ready)
            print(
                f"\n  !! Timed out after {READY_TIMEOUT_S}s waiting for: {waiting}. "
                "Check the logs in services/ai/demo_console/_logs/."
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
    parser = argparse.ArgumentParser(description="Start ASR + MT + demo console.")
    parser.add_argument("--no-browser", action="store_true", help="Just print the URL.")
    args = parser.parse_args(argv)

    services = [
        Service("ASR (speech)", "speech.app", SpeechSettings.from_env().port, "/health/ready"),
        Service("MT (pivot)", "mt.app", MtSettings.from_env().port, "/health/ready"),
        Service("Demo console", "demo_console.app", DEMO_PORT, "/health"),
    ]
    python = _python_exe()
    print(f"Starting SatSandesh AI demo (python: {python})")
    launched_at = time.monotonic()
    for svc in services:
        _start(svc, python)

    try:
        if not _wait_ready(services, launched_at):
            print("Demo is NOT ready. Fix the problem above and run this again.")
            _stop_started(services)
            return 1

        url = f"http://127.0.0.1:{DEMO_PORT}/"
        print("\n" + "=" * 60)
        print(f"  DEMO READY:  {url}")
        print("=" * 60)
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
