"""Tests for the browser-mic upload, the render stage, the audio route, the
extended /health/deps and the launcher's service list.

CI-safe: no models, no microphone, no network. Upstream HTTP is faked with
httpx.MockTransport exactly like test_demo_console.py.
"""

from __future__ import annotations

import io
import json
import re
import uuid
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from services.ai.demo_console import app as console
from services.ai.demo_console import start_demo

FAKE_TOKEN = "hf_FAKE_TOKEN_for_tests_0123456789"


@pytest.fixture
def client() -> TestClient:
    return TestClient(console.app)


def _fake_upstream(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(console.httpx, "AsyncClient", factory)


def make_wav(
    seconds: float = 1.0,
    *,
    rate: int = 16000,
    channels: int = 1,
    width: int = 2,
    amplitude: int = 8000,
) -> bytes:
    n = int(seconds * rate)
    if width == 2:
        samples = (np.sin(np.arange(n) / 7) * amplitude).astype("<i2")
        frames = np.repeat(samples, channels).tobytes()
    else:
        frames = bytes([128]) * (n * channels * width)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)
    return buf.getvalue()


@pytest.fixture
def rec_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "rec.wav"
    monkeypatch.setattr(console, "RECORDING_PATH", path)
    return path


# --------------------------------------------------------------------------
# POST /pipeline/upload
# --------------------------------------------------------------------------


def _upload(client: TestClient, body: bytes):
    return client.post("/pipeline/upload", content=body, headers={"Content-Type": "audio/wav"})


def test_upload_valid_wav(client: TestClient, rec_path: Path) -> None:
    wav = make_wav(2.0)
    resp = _upload(client, wav)
    assert resp.status_code == 200
    body = resp.json()
    assert body["duration_s"] == 2.0
    assert 7000 < body["peak"] <= 8000
    assert rec_path.read_bytes() == wav


@pytest.mark.parametrize(
    ("body", "title_part"),
    [
        (b"", "empty"),
        (b"this is definitely not a wav file", "WAV"),
        (b"RIFF" + b"\x00" * 60, "WAV"),
        (make_wav(rate=44100), "16 kHz"),
        (make_wav(channels=2), "mono"),
        (make_wav(width=1), "16-bit"),
    ],
    ids=["empty", "text", "riff-garbage", "44k", "stereo", "8bit"],
)
def test_upload_rejects_bad_audio(
    client: TestClient, rec_path: Path, body: bytes, title_part: str
) -> None:
    rec_path.write_bytes(b"previous good recording")
    resp = _upload(client, body)
    assert resp.status_code in (400, 413, 415, 422)
    err = resp.json()["error"]
    assert err["stage"] == "upload"
    assert title_part.lower() in (err["title"] + err["message"]).lower()
    assert rec_path.read_bytes() == b"previous good recording"  # untouched on failure


def test_upload_rejects_oversized_body(
    client: TestClient, rec_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(console, "MAX_UPLOAD_BYTES", 10_000)
    resp = _upload(client, make_wav(2.0))  # ~64 KB
    assert resp.status_code == 413
    assert not rec_path.exists()


def test_upload_rejects_too_long(
    client: TestClient, rec_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(console, "MAX_RECORD_SECONDS", 2.0)
    resp = _upload(client, make_wav(5.0))
    assert resp.status_code == 422
    assert "too long" in resp.json()["error"]["title"].lower()
    assert not rec_path.exists()


def test_upload_flags_silence(client: TestClient, rec_path: Path) -> None:
    resp = _upload(client, make_wav(1.0, amplitude=0))
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "Recording was silent"
    assert not rec_path.exists()


def test_upload_quiet_but_above_threshold_is_accepted(client: TestClient, rec_path: Path) -> None:
    resp = _upload(client, make_wav(1.0, amplitude=console.SILENCE_PEAK_THRESHOLD + 100))
    assert resp.status_code == 200
    assert rec_path.exists()


# --------------------------------------------------------------------------
# POST /pipeline/render
# --------------------------------------------------------------------------


def _render_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "render_out"
    out.mkdir()
    monkeypatch.setattr(console, "RENDER_OUTPUT_DIR", out)
    return out


def _make_audio(out: Path, lang: str = "te") -> Path:
    path = out / f"render-{uuid.uuid4().hex}-{lang}.wav"
    path.write_bytes(make_wav(1.0))
    return path


def _render_response(
    text: str = "నమస్కారం",
    uri: str = "",
    *,
    lang: str = "te",
    degraded: dict | None = None,
    duration_ms: float = 2500.0,
) -> dict:
    return {
        "contract_version": "0.1.0",
        "results": [
            {
                "language": lang,
                "text": text,
                "audio": {
                    "uri": uri,
                    "format": "wav_pcm16",
                    "duration_ms": 1000 if uri else 0,
                    "sample_rate_hz": 22050 if uri else None,
                },
                "model_version_translate": "it2-en-indic",
                "model_version_tts": "piper-te",
                "duration_ms": duration_ms,
                "degraded": degraded or {"active": False, "reason": "none", "detail": None},
            }
        ],
        "degraded": degraded or {"active": False, "reason": "none", "detail": None},
    }


def test_render_success(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _render_dir(tmp_path, monkeypatch)
    audio = _make_audio(out)
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["json"] = json.loads(req.content)
        return httpx.Response(200, json=_render_response(uri=audio.resolve().as_uri()))

    _fake_upstream(monkeypatch, handler)
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "te"})
    assert resp.status_code == 200
    body = resp.json()
    assert seen["path"] == "/v1/render"
    assert seen["json"] == {"pivot_text": "Hello", "target_languages": ["te"]}
    assert body["text"] == "నమస్కారం"
    assert body["language"] == "te"
    assert body["audio_url"] == f"/pipeline/audio/{audio.name}"
    assert body["audio_duration_ms"] == 1000
    assert body["model_version_translate"] == "it2-en-indic"
    assert body["model_version_tts"] == "piper-te"
    assert body["degraded"]["active"] is False
    assert body["latency_ms"]["service_total"] == 2500.0
    assert body["latency_ms"]["round_trip"] >= 0
    assert set(body["latency_ms"]) == {"service_total", "round_trip"}  # no invented split


@pytest.mark.parametrize("reason", ["text_only", "tts_skipped"])
def test_render_degraded_is_a_normal_response(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _render_dir(tmp_path, monkeypatch)
    degraded = {"active": True, "reason": reason, "detail": "TTS failed: RuntimeError"}
    _fake_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json=_render_response(lang="hi", degraded=degraded)),
    )
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"]
    assert body["audio_url"] is None
    assert body["degraded"] == degraded


def test_render_when_not_running(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    _fake_upstream(monkeypatch, handler)
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "te"})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["stage"] == "render"
    assert "not running" in err["title"]


def test_render_timeout(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    _fake_upstream(monkeypatch, handler)
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "te"})
    assert resp.status_code == 504
    assert "took too long" in resp.json()["error"]["title"]


def test_render_warming_up(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_upstream(
        monkeypatch,
        lambda req: httpx.Response(503, json={"code": "MODEL_LOAD_FAILED", "message": "not ready"}),
    )
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "te"})
    assert resp.status_code == 503
    assert "warming up" in resp.json()["error"]["title"]


def test_render_empty_text(client: TestClient) -> None:
    resp = client.post("/pipeline/render", json={"text": "   ", "target_language": "te"})
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "Nothing to say"


def test_render_unsupported_language(client: TestClient) -> None:
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "ta"})
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "Unsupported language"


def test_render_audio_outside_output_dir_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _render_dir(tmp_path, monkeypatch)
    elsewhere = tmp_path / f"render-{uuid.uuid4().hex}-te.wav"
    elsewhere.write_bytes(make_wav(0.5))
    _fake_upstream(
        monkeypatch,
        lambda req: httpx.Response(200, json=_render_response(uri=elsewhere.resolve().as_uri())),
    )
    resp = client.post("/pipeline/render", json={"text": "Hello", "target_language": "te"})
    assert resp.status_code == 502
    assert "unexpected location" in resp.json()["error"]["message"]


# --------------------------------------------------------------------------
# GET /pipeline/audio/{name}
# --------------------------------------------------------------------------


def test_audio_served(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = _render_dir(tmp_path, monkeypatch)
    audio = _make_audio(out, "hi")
    resp = client.get(f"/pipeline/audio/{audio.name}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/")
    assert resp.content == audio.read_bytes()


def test_audio_name_pattern_is_the_documented_whitelist() -> None:
    assert re.compile(console.AUDIO_NAME_PATTERN).pattern == r"^render-[0-9a-f]{32}-(te|hi)\.wav$"


def test_audio_missing_file(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _render_dir(tmp_path, monkeypatch)
    assert client.get(f"/pipeline/audio/render-{'a' * 32}-te.wav").status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "..%2F..%2Fapp.py",
        "%2e%2e/%2e%2e/app.py",
        "..%5C..%5Capp.py",
        "%2Fetc%2Fpasswd",
        "C:%5CWindows%5Cwin.ini",
        "../render-" + "a" * 32 + "-te.wav",
        "render-" + "a" * 32 + "-te.mp3",
        "render-" + "A" * 32 + "-te.wav",
        "render-" + "a" * 32 + "-en.wav",
        "render-" + "a" * 31 + "-te.wav",
        ".warmup_te.wav",
        "secret.txt",
    ],
)
def test_audio_rejects_traversal_and_foreign_names(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    out = _render_dir(tmp_path, monkeypatch)
    (out / ".warmup_te.wav").write_bytes(make_wav(0.1))
    (out / "secret.txt").write_text("nope")
    (tmp_path / f"render-{'a' * 32}-te.wav").write_bytes(make_wav(0.1))  # sibling of out/
    assert client.get(f"/pipeline/audio/{name}").status_code == 404


def test_audio_symlink_escaping_output_dir_is_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _render_dir(tmp_path, monkeypatch)
    target = tmp_path / "outside.wav"
    target.write_bytes(make_wav(0.1))
    link = out / f"render-{'b' * 32}-te.wav"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this machine")
    assert client.get(f"/pipeline/audio/{link.name}").status_code == 404


# --------------------------------------------------------------------------
# /health/deps
# --------------------------------------------------------------------------


def test_health_deps_reports_render_and_versions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(console, "ASR", console.Upstream("ASR", "http://127.0.0.1:18002", "x"))
    monkeypatch.setattr(console, "MT", console.Upstream("MT", "http://127.0.0.1:18004", "x"))
    monkeypatch.setattr(
        console, "RENDER", console.Upstream("Render", "http://127.0.0.1:18006", "x")
    )

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.port == 18002:
            return httpx.Response(200, json={"status": "ready", "model_version": "asr-v1"})
        if req.url.port == 18004:
            return httpx.Response(503, json={"status": "not_ready"})
        raise httpx.ConnectError("refused", request=req)

    _fake_upstream(monkeypatch, handler)
    body = client.get("/health/deps").json()
    assert body["asr"]["state"] == "ready"
    assert body["asr"]["model"] == "asr-v1"
    assert body["mt"]["state"] == "warming_up"
    assert body["render"]["state"] == "down"
    assert "max_record_seconds" in body


def test_health_deps_render_ready_carries_translate_and_voice_versions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = {
        "status": "ready",
        "mt_model_version": "it2-en-indic",
        "tts_voices": {"hi": "piper-hi", "te": "piper-te"},
    }
    _fake_upstream(monkeypatch, lambda req: httpx.Response(200, json=ready))
    body = client.get("/health/deps").json()
    assert body["render"]["state"] == "ready"
    assert body["render"]["model"] == "it2-en-indic"
    assert body["render"]["tts_voices"] == {"hi": "piper-hi", "te": "piper-te"}


def test_pipeline_stage_order() -> None:
    assert [s.id for s in console.PIPELINE_STAGES] == [
        "record",
        "transcribe",
        "translate",
        "render",
    ]


def test_default_ports_do_not_collide() -> None:
    assert console.DEMO_PORT == 8005
    assert console.RENDER_BASE_URL.endswith(":8006")


# --------------------------------------------------------------------------
# Launcher
# --------------------------------------------------------------------------


def _by_name(services: list[start_demo.Service]) -> dict[str, start_demo.Service]:
    return {s.module.split(".")[0]: s for s in services}


def test_launcher_default_service_list() -> None:
    services = start_demo.build_services()
    assert [s.module for s in services] == [
        "speech_indicconformer.app",
        "mt.app",
        "render.app",
        "demo_console.app",
    ]
    asr, mt, render, con = services
    assert (asr.port, mt.port, render.port, con.port) == (8002, 8004, 8006, 8005)
    assert asr.extra_env["ASR_PORT"] == "8002"
    assert render.extra_env["RENDER_PORT"] == "8006"
    assert [s.ready_path for s in services] == ["/health/ready"] * 3 + ["/health"]


def test_launcher_ports_are_distinct() -> None:
    for asr in ("indicconformer", "whisper"):
        ports = [s.port for s in start_demo.build_services(asr)]
        assert len(set(ports)) == len(ports)


def test_launcher_whisper_fallback() -> None:
    services = start_demo.build_services("whisper")
    assert services[0].module == "speech.app"
    assert services[0].port == 8002
    assert services[0].extra_env["ASR_PORT"] == "8002"


def test_launcher_child_env_offline_and_token_passthrough() -> None:
    base = {"HF_TOKEN": FAKE_TOKEN, "PATH": "x", "ASR_PORT": "8004", "RENDER_PORT": "8005"}
    svc = start_demo.build_services()[2]
    env = start_demo.child_env(svc, base)
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["HF_TOKEN"] == FAKE_TOKEN
    assert env["RENDER_PORT"] == "8006"
    # a stale ASR_PORT from the user's shell must not leak into the other children
    assert env["ASR_PORT"] == "8002"


def test_launcher_child_env_without_token_has_none() -> None:
    env = start_demo.child_env(start_demo.build_services()[1], {"PATH": "x"})
    assert "HF_TOKEN" not in env


def test_asr_identity_warning() -> None:
    conformer = "ai4bharat/indic-conformer-600m-multilingual-onnx-ctc"
    whisper = "faster-whisper-small-int8@1.0"
    assert start_demo.asr_identity_warning(conformer, "indicconformer") is None
    assert start_demo.asr_identity_warning(whisper, "whisper") is None
    loud = start_demo.asr_identity_warning(whisper, "indicconformer")
    assert loud is not None and "WARNING" in loud and whisper in loud
    assert start_demo.asr_identity_warning(conformer, "whisper") is not None
    assert start_demo.asr_identity_warning(None, "indicconformer") is None


def test_missing_token_notice_names_the_services() -> None:
    notice = start_demo.missing_token_notice(start_demo.build_services(), {})
    assert notice is not None
    assert "HF_TOKEN" in notice
    assert "render" in notice.lower() and "conformer" in notice.lower()
    assert start_demo.missing_token_notice(start_demo.build_services(), {"HF_TOKEN": "x"}) is None
    # whisper + MT do not need it; only render does
    notice_w = start_demo.missing_token_notice(start_demo.build_services("whisper"), {})
    assert notice_w is not None and "conformer" not in notice_w.lower()


def test_launcher_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(start_demo, "LOG_DIR", tmp_path)
    started_ports: set[int] = set()
    spawned_envs: list[dict] = []

    class FakeProc:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        started_ports.add(int(cmd[cmd.index("--port") + 1]))
        spawned_envs.append(kwargs["env"])
        return FakeProc()

    def fake_status(url: str, timeout: float = 2.0):
        port = int(url.split(":")[2].split("/")[0])
        return 200 if port in started_ports else None

    def stop_after_ready(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(start_demo.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(start_demo, "_http_status", fake_status)
    monkeypatch.setattr(start_demo, "_python_exe", lambda: "python")
    monkeypatch.setattr(start_demo.time, "sleep", stop_after_ready)
    assert start_demo.main(["--no-browser"]) == 0
    out = capsys.readouterr()
    assert FAKE_TOKEN not in out.out + out.err
    assert "DEMO READY" in out.out
    assert started_ports == {8002, 8004, 8005, 8006}
    assert all(e["HF_TOKEN"] == FAKE_TOKEN for e in spawned_envs)
    assert all(e["HF_HUB_OFFLINE"] == "1" for e in spawned_envs)
