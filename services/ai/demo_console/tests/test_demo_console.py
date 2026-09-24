"""Error-path tests for the demo console: every failure must come back as a
clean {"error": {stage, title, message}} body, never a raw 500/stack trace.

No real ASR/MT service, model or microphone is needed — upstream HTTP is
faked with httpx.MockTransport and the recorder is monkeypatched.
"""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from services.ai.demo_console import app as console
from services.ai.tools import record_sample


@pytest.fixture
def client() -> TestClient:
    return TestClient(console.app)


def _fake_upstream(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(console.httpx, "AsyncClient", factory)


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    wav = tmp_path / "rec.wav"
    wav.write_bytes(b"RIFF")
    monkeypatch.setattr(console, "RECORDING_PATH", wav)
    return wav


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_index_served(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Record (7s)" in resp.text


def test_transcribe_when_asr_not_running(
    client: TestClient, recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    down = console.Upstream("Speech recognition (ASR)", f"http://127.0.0.1:{_closed_port()}", "x")
    monkeypatch.setattr(console, "ASR", down)
    resp = client.post("/pipeline/transcribe", json={"source_language": "te"})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["stage"] == "transcribe"
    assert "not running" in err["title"]


def test_transcribe_when_asr_warming_up(
    client: TestClient, recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_upstream(
        monkeypatch,
        lambda req: httpx.Response(
            503, json={"code": "MODEL_LOAD_FAILED", "message": "ASR model is not ready yet"}
        ),
    )
    resp = client.post("/pipeline/transcribe", json={"source_language": "te"})
    assert resp.status_code == 503
    assert "warming up" in resp.json()["error"]["title"]


def test_transcribe_success_passes_language_hint(
    client: TestClient, recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "text": " నమస్కారం ",
                "detected_language": "te",
                "model_version": "m",
                "duration_ms": 900.0,
                "stage_timings": [
                    {"stage": "decode", "duration_ms": 5.0},
                    {"stage": "inference", "duration_ms": 890.0},
                    {"stage": "postprocess", "duration_ms": 5.0},
                ],
            },
        )

    _fake_upstream(monkeypatch, handler)
    resp = client.post("/pipeline/transcribe", json={"source_language": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "నమస్కారం"
    assert body["latency_ms"]["inference"] == 890.0
    assert seen["language_hint"] == "hi"
    assert seen["audio"]["format"] == "wav_pcm16"


def test_transcribe_empty_text_is_clear_error(
    client: TestClient, recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_upstream(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "text": "  ",
                "detected_language": "te",
                "model_version": "m",
                "duration_ms": 1.0,
            },
        ),
    )
    resp = client.post("/pipeline/transcribe", json={"source_language": "te"})
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "No speech recognised"


def test_transcribe_timeout(
    client: TestClient, recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    _fake_upstream(monkeypatch, handler)
    resp = client.post("/pipeline/transcribe", json={"source_language": "te"})
    assert resp.status_code == 504
    assert "took too long" in resp.json()["error"]["title"]


def test_translate_when_mt_not_running(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    down = console.Upstream("Translation (MT)", f"http://127.0.0.1:{_closed_port()}", "x")
    monkeypatch.setattr(console, "MT", down)
    resp = client.post("/pipeline/translate", json={"text": "नमस्ते", "source_language": "hi"})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["stage"] == "translate"
    assert "Translation (MT) service is not running" == err["title"]


def test_translate_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_upstream(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "pivot_text": "Hello",
                "source_language": "hi",
                "model_version": "it2",
                "duration_ms": 321.0,
            },
        ),
    )
    resp = client.post("/pipeline/translate", json={"text": "नमस्ते", "source_language": "hi"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "Hello"
    assert resp.json()["latency_ms"]["service_total"] == 321.0


def test_unsupported_language(client: TestClient, recorded: Path) -> None:
    resp = client.post("/pipeline/transcribe", json={"source_language": "ta"})
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "Unsupported language"


def test_bad_request_body_is_wrapped(client: TestClient) -> None:
    resp = client.post("/pipeline/translate", json={})
    assert resp.status_code == 422
    assert "error" in resp.json()


def test_record_silent(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console, "RECORDING_PATH", tmp_path / "r.wav")
    monkeypatch.setattr(record_sample, "check_input_device_available", lambda: None)
    monkeypatch.setattr(record_sample, "record", lambda s: np.zeros((1600, 1), dtype=np.int16))
    monkeypatch.setattr(console, "RECORD_SECONDS", 0.1)
    resp = client.post("/pipeline/record", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["title"] == "Recording was silent"


def test_record_ok(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console, "RECORDING_PATH", tmp_path / "r.wav")
    monkeypatch.setattr(record_sample, "check_input_device_available", lambda: None)
    audio = (np.sin(np.arange(1600) / 5) * 8000).astype(np.int16).reshape(-1, 1)
    monkeypatch.setattr(record_sample, "record", lambda s: audio)
    monkeypatch.setattr(console, "RECORD_SECONDS", 0.1)
    resp = client.post("/pipeline/record", json={})
    assert resp.status_code == 200
    assert resp.json()["peak_amplitude"] > 7000
    assert (tmp_path / "r.wav").exists()


def test_record_no_mic(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_mic() -> None:
        raise record_sample.NoInputDeviceError("no default input device")

    monkeypatch.setattr(record_sample, "check_input_device_available", no_mic)
    resp = client.post("/pipeline/record", json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["title"] == "No microphone found"
