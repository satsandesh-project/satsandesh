"""English replies are spoken by a local, demo-only synthesizer (Windows built-in voice).

CI-safe: the synthesizer is faked, so no audio device, no Windows and no voice are needed.
The two tests that run the real engine are skipped anywhere it is not available.
"""

from __future__ import annotations

import io
import subprocess
import sys
import uuid
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from services.ai.demo_console import app as console
from services.ai.demo_console import english_tts, start_demo

from .test_full_pipeline import _fake_upstream, make_wav


@pytest.fixture
def client() -> TestClient:
    return TestClient(console.app)


@pytest.fixture
def en_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "_audio"
    monkeypatch.setattr(console, "EN_AUDIO_DIR", out)
    monkeypatch.setattr(console, "EN_VOICE_ENABLED", True)
    return out


@pytest.fixture
def render_never_called(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url)
        return httpx.Response(500)

    _fake_upstream(monkeypatch, handler)
    return calls


class FakeSynth:
    """Stands in for english_tts.synthesize and records what it was asked."""

    def __init__(self, voice: str = "Fake Voice", seconds: float = 1.5) -> None:
        self.voice, self.seconds, self.calls = voice, seconds, []

    def __call__(self, text: str, out_path: Path, *, timeout_s: float = 30.0):
        self.calls.append((text, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(make_wav(self.seconds, rate=16000))
        return english_tts.EnglishSpeech(voice=self.voice, duration_ms=int(self.seconds * 1000))


def _reply(client: TestClient, text: str = "Take care"):
    return client.post("/pipeline/render", json={"text": text, "target_language": "en"})


# --------------------------------------------------------------------------
# The reply stage
# --------------------------------------------------------------------------


def test_english_reply_is_spoken_locally_and_render_is_not_called(
    client, en_dir, render_never_called, monkeypatch
) -> None:
    synth = FakeSynth(voice="Microsoft Zira Desktop", seconds=2.0)
    monkeypatch.setattr(english_tts, "synthesize", synth)
    resp = _reply(client, " Take care ")
    assert resp.status_code == 200
    body = resp.json()
    assert render_never_called == []
    assert synth.calls[0][0] == "Take care"
    assert body["text"] == "Take care" and body["language"] == "en"
    assert console.EN_AUDIO_RE.fullmatch(body["audio_url"].rsplit("/", 1)[-1])
    assert body["audio_duration_ms"] == 2000
    assert body["degraded"] == {"active": False, "reason": None, "detail": None}
    assert body["model_version_tts"] == "windows-sapi:Microsoft Zira Desktop"
    assert body["model_version_translate"] is None
    assert body["skipped_render"] is True
    assert body["latency_ms"]["service_total"] >= 0 and body["latency_ms"]["round_trip"] >= 0
    audio = client.get(body["audio_url"])
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(audio.content)) as w:
        assert w.getnframes() > 0


@pytest.mark.parametrize(
    "failure",
    [
        english_tts.EnglishVoiceUnavailable("no voice installed"),
        english_tts.EnglishVoiceUnavailable("not Windows"),
        RuntimeError("the engine blew up"),
        subprocess.TimeoutExpired("powershell", 30),
    ],
    ids=["no-voice", "not-windows", "crash", "timeout"],
)
def test_synthesizer_failure_falls_back_to_text_only_never_an_error(
    client, en_dir, render_never_called, monkeypatch, failure
) -> None:
    def broken(text, out_path, *, timeout_s=30.0):
        raise failure

    monkeypatch.setattr(english_tts, "synthesize", broken)
    resp = _reply(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "Take care" and body["audio_url"] is None
    assert body["audio_duration_ms"] is None and body["model_version_tts"] is None
    assert body["degraded"]["active"] is True and body["degraded"]["reason"] == "NO_ENGLISH_VOICE"
    assert "Traceback" not in resp.text
    assert render_never_called == []


def test_synthesizer_that_writes_nothing_is_text_only(
    client, en_dir, render_never_called, monkeypatch
) -> None:
    monkeypatch.setattr(
        english_tts,
        "synthesize",
        lambda text, out_path, *, timeout_s=30.0: english_tts.EnglishSpeech("V", 1000),
    )
    body = _reply(client).json()
    assert body["audio_url"] is None and body["degraded"]["active"] is True


def test_voice_off_never_calls_the_synthesizer(
    client, en_dir, render_never_called, monkeypatch
) -> None:
    synth = FakeSynth()
    monkeypatch.setattr(english_tts, "synthesize", synth)
    monkeypatch.setattr(console, "EN_VOICE_ENABLED", False)
    body = _reply(client).json()
    assert synth.calls == [] and body["audio_url"] is None
    assert body["degraded"]["reason"] == "NO_ENGLISH_VOICE"


def test_empty_english_reply_is_a_demo_error(client, en_dir, monkeypatch) -> None:
    synth = FakeSynth()
    monkeypatch.setattr(english_tts, "synthesize", synth)
    resp = _reply(client, "   ")
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "Nothing to say"
    assert synth.calls == []


def test_overlong_english_reply_is_refused_calmly(client, en_dir, monkeypatch) -> None:
    synth = FakeSynth()
    monkeypatch.setattr(english_tts, "synthesize", synth)
    resp = _reply(client, "x" * (english_tts.MAX_CHARS + 1))
    assert resp.status_code == 422 and "too long" in resp.json()["error"]["title"].lower()
    assert synth.calls == []


def test_old_english_audio_is_pruned(client, en_dir, render_never_called, monkeypatch) -> None:
    monkeypatch.setattr(english_tts, "synthesize", FakeSynth())
    monkeypatch.setattr(console, "EN_AUDIO_KEEP", 3)
    for _ in range(6):
        assert _reply(client).status_code == 200
    assert len(list(en_dir.glob("en-*.wav"))) == 3


# --------------------------------------------------------------------------
# The audio route: strict whitelist per directory
# --------------------------------------------------------------------------


def _en_name() -> str:
    return f"en-{uuid.uuid4().hex}.wav"


def test_english_audio_name_whitelist_is_strict() -> None:
    assert console.EN_AUDIO_NAME_PATTERN == r"^en-[0-9a-f]{32}\.wav$"
    good = _en_name()
    assert console.EN_AUDIO_RE.fullmatch(good)
    for bad in (
        "en-abc.wav",
        good + ".exe",
        good[:-4] + ".wav\n",
        "EN-" + good[3:],
        f"en-{'g' * 32}.wav",
        f"render-{uuid.uuid4().hex}-te.wav",
        "../" + good,
    ):
        assert not console.EN_AUDIO_RE.fullmatch(bad), bad


def test_render_whitelist_is_unchanged() -> None:
    assert console.AUDIO_NAME_PATTERN == r"^render-[0-9a-f]{32}-(te|hi)\.wav$"


def test_english_audio_served_from_its_own_directory_only(
    client, en_dir, tmp_path, monkeypatch
) -> None:
    render_dir = tmp_path / "render_out"
    render_dir.mkdir()
    monkeypatch.setattr(console, "RENDER_OUTPUT_DIR", render_dir)
    en_dir.mkdir()
    name = _en_name()
    (en_dir / name).write_bytes(make_wav(0.5))
    assert client.get(f"/pipeline/audio/{name}").status_code == 200
    # an English-named file in render's directory, or a render-named one in ours, is not served
    stray = _en_name()
    (render_dir / stray).write_bytes(make_wav(0.5))
    assert client.get(f"/pipeline/audio/{stray}").status_code == 404
    render_name = f"render-{uuid.uuid4().hex}-te.wav"
    (en_dir / render_name).write_bytes(make_wav(0.5))
    assert client.get(f"/pipeline/audio/{render_name}").status_code == 404
    # render's own route still works
    (render_dir / render_name).write_bytes(make_wav(0.5))
    assert client.get(f"/pipeline/audio/{render_name}").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "../_last_recording.wav",
        "..%2f_last_recording.wav",
        "%2e%2e%2fapp.py",
        "en-../app.py",
        "en-00000000000000000000000000000000.wav/../../app.py",
        "en-00000000000000000000000000000000.WAV",
        "en-0000000000000000000000000000000.wav",
        "%2fetc%2fpasswd",
    ],
)
def test_english_audio_route_rejects_traversal_and_foreign_names(client, en_dir, path) -> None:
    en_dir.mkdir()
    (en_dir.parent / "app.py").write_text("secret")
    assert client.get(f"/pipeline/audio/{path}").status_code in (404, 405)


def test_missing_english_file_is_404(client, en_dir) -> None:
    assert client.get(f"/pipeline/audio/{_en_name()}").status_code == 404


def test_english_audio_symlink_escaping_the_directory_is_refused(client, en_dir, tmp_path) -> None:
    en_dir.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(make_wav(0.5))
    link = en_dir / _en_name()
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here")
    assert client.get(f"/pipeline/audio/{link.name}").status_code == 404


def test_audio_directory_is_gitignored_and_never_a_tracked_path() -> None:
    ignore = (Path(console.__file__).parent / ".gitignore").read_text(encoding="utf-8")
    assert "_audio/" in ignore.split()
    assert console.EN_AUDIO_DIR.name == "_audio"


# --------------------------------------------------------------------------
# The synthesizer module itself (no real engine)
# --------------------------------------------------------------------------


def test_synthesize_refuses_when_not_windows(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(english_tts.sys, "platform", "linux")
    with pytest.raises(english_tts.EnglishVoiceUnavailable):
        english_tts.synthesize("hello", tmp_path / "x.wav")


def test_text_never_reaches_the_command_line(monkeypatch, tmp_path) -> None:
    """The reply text travels in the environment, not in the command string."""
    monkeypatch.setattr(english_tts.sys, "platform", "win32")
    seen: dict = {}
    nasty = "hi\"; Remove-Item -Recurse C:\\ ; $(calc) `whoami` ' --"

    def fake_run(cmd, **kwargs):
        seen["cmd"], seen["env"], seen["timeout"] = cmd, kwargs["env"], kwargs["timeout"]
        out = Path(kwargs["env"]["SAT_TTS_OUT"])
        out.write_bytes(make_wav(1.0))
        return subprocess.CompletedProcess(cmd, 0, stdout="Voice Name\n", stderr="")

    monkeypatch.setattr(english_tts.subprocess, "run", fake_run)
    result = english_tts.synthesize(nasty, tmp_path / "o.wav", timeout_s=7)
    assert result.voice == "Voice Name" and result.duration_ms == 1000
    assert all("Remove-Item" not in part and nasty not in part for part in seen["cmd"])
    assert seen["env"]["SAT_TTS_TEXT"] == nasty
    assert seen["timeout"] == 7
    assert "-NonInteractive" in seen["cmd"] and "-NoProfile" in seen["cmd"]


def test_engine_failure_and_timeout_are_reported_as_unavailable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(english_tts.sys, "platform", "win32")

    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 3, stdout="", stderr="NO_ENGLISH_VOICE")

    monkeypatch.setattr(english_tts.subprocess, "run", failing)
    with pytest.raises(english_tts.EnglishVoiceUnavailable):
        english_tts.synthesize("hi", tmp_path / "o.wav")

    def hangs(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(english_tts.subprocess, "run", hangs)
    with pytest.raises(english_tts.EnglishVoiceUnavailable):
        english_tts.synthesize("hi", tmp_path / "o.wav")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows built-in speech engine only")
def test_real_windows_voice_makes_a_valid_16bit_wav(tmp_path) -> None:
    out = tmp_path / "real.wav"
    try:
        result = english_tts.synthesize("Hello, this is a test.", out, timeout_s=60)
    except english_tts.EnglishVoiceUnavailable as exc:
        pytest.skip(f"no usable Windows English voice: {exc}")
    assert result.voice
    with wave.open(str(out)) as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getnframes() > 0
    assert 500 < result.duration_ms < 10_000


# --------------------------------------------------------------------------
# Launcher: --en-voice
# --------------------------------------------------------------------------


def test_launcher_en_voice_flag_defaults_to_windows_and_is_validated() -> None:
    assert start_demo.parse_args([]).en_voice == "windows"
    assert start_demo.parse_args(["--en-voice", "off"]).en_voice == "off"
    with pytest.raises(SystemExit):
        start_demo.parse_args(["--en-voice", "piper"])


def test_launcher_passes_en_voice_to_the_console_only() -> None:
    for mode in ("windows", "off"):
        services = start_demo.build_services("indicconformer", "whisper", en_voice=mode)
        assert start_demo.child_env(services[-1], {})["DEMO_EN_VOICE"] == mode
        assert "DEMO_EN_VOICE" not in start_demo.child_env(services[0], {})
    # a stale value in the shell cannot override the flag
    services = start_demo.build_services("indicconformer", "whisper", en_voice="off")
    assert (
        start_demo.child_env(services[-1], {"DEMO_EN_VOICE": "windows"})["DEMO_EN_VOICE"] == "off"
    )


def test_health_deps_reports_the_english_voice(client, monkeypatch) -> None:
    _fake_upstream(monkeypatch, lambda req: httpx.Response(503))
    monkeypatch.setattr(console, "EN_VOICE_ENABLED", True)
    assert client.get("/health/deps").json()["en_voice"]["state"] in ("ready", "unavailable")
    monkeypatch.setattr(console, "EN_VOICE_ENABLED", False)
    assert client.get("/health/deps").json()["en_voice"]["state"] == "off"
