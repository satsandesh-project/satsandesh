"""
Tests for the real ASR service under services/ai/speech/. This loads the
actual faster-whisper model once per test session — unlike
services/ai/mock/app.py, these tests take real seconds, not milliseconds,
because there is no mock here to fall back to.
"""

import pytest
import services.ai.speech.app as speech_app
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.transcribe import TranscribeResponse
from fastapi.testclient import TestClient

_FIXTURES_DIR = speech_app._WARMUP_AUDIO_PATH.parent
TONE_FIXTURE_URI = "file:///" + str(speech_app._WARMUP_AUDIO_PATH).replace("\\", "/")


def _fixture_uri(name: str) -> str:
    return "file:///" + str(_FIXTURES_DIR / name).replace("\\", "/")


@pytest.fixture(scope="module")
def client():
    with TestClient(speech_app.app) as c:
        yield c


def _transcribe_payload(
    audio_format: str = "wav_pcm16",
    language_hint: str | None = "en",
    uri: str | None = None,
) -> dict:
    payload = {
        "audio": {
            "uri": uri if uri is not None else TONE_FIXTURE_URI,
            "format": audio_format,
            "duration_ms": 2000,
            "sample_rate_hz": 16000,
        }
    }
    if language_hint is not None:
        payload["language_hint"] = language_hint
    return payload


def test_health_live_is_always_200(client: TestClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "live"}


def test_health_ready_reflects_load_state(client: TestClient) -> None:
    # `client` is module-scoped and entered as a context manager, so by the
    # time any test receives it, the app's lifespan startup (model load +
    # warm-up) has already run to completion — there is no way to observe a
    # genuinely cold, not-yet-ready process through this same client without
    # tearing it down and reloading the model per test, which would make the
    # suite reload a ~small Whisper model on every single test. Instead this
    # flips the module-level `_ready` flag directly to simulate the not-ready
    # window, exercises the endpoint, then restores it.
    assert speech_app._ready is True
    ready_resp = client.get("/health/ready")
    assert ready_resp.status_code == 200
    body = ready_resp.json()
    assert body["status"] == "ready"
    assert body["model_version"] == speech_app.engine.model_version
    assert body["load_duration_ms"] is not None
    assert body["warmup_duration_ms"] is not None

    speech_app._ready = False
    try:
        not_ready_resp = client.get("/health/ready")
        assert not_ready_resp.status_code == 503
        assert not_ready_resp.json() == {"status": "not_ready"}
    finally:
        speech_app._ready = True


def test_transcribe_wav_returns_well_formed_response(client: TestClient) -> None:
    resp = client.post("/v1/transcribe", json=_transcribe_payload())
    assert resp.status_code == 200

    body = resp.json()
    parsed = TranscribeResponse.model_validate(body)  # raises if the shape is wrong
    assert parsed.detected_language.value == "en"
    assert parsed.model_version == speech_app.engine.model_version
    assert parsed.duration_ms >= 0
    stage_names = [s.stage for s in parsed.stage_timings]
    assert stage_names == ["decode", "inference", "postprocess"]
    assert all(s.duration_ms >= 0 for s in parsed.stage_timings)
    assert parsed.degraded.active is False


def test_transcribe_missing_file_returns_pipeline_error(client: TestClient) -> None:
    payload = _transcribe_payload()
    payload["audio"]["uri"] = "file:///C:/does/not/exist.wav"
    resp = client.post("/v1/transcribe", json=payload)
    assert resp.status_code == 422

    parsed = PipelineError.model_validate(resp.json())
    assert parsed.code == ErrorCode.AUDIO_FETCH_FAILED


def test_transcribe_ogg_opus_returns_well_formed_response(client: TestClient) -> None:
    payload = _transcribe_payload(audio_format="ogg_opus", uri=_fixture_uri("tone_2s.opus"))
    resp = client.post("/v1/transcribe", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    parsed = TranscribeResponse.model_validate(body)
    assert parsed.detected_language.value == "en"
    stage_names = [s.stage for s in parsed.stage_timings]
    assert stage_names == ["decode", "inference", "postprocess"]
    assert parsed.degraded.active is False


def test_transcribe_mp3_returns_well_formed_response(client: TestClient) -> None:
    payload = _transcribe_payload(audio_format="mp3", uri=_fixture_uri("tone_2s.mp3"))
    resp = client.post("/v1/transcribe", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    parsed = TranscribeResponse.model_validate(body)
    assert parsed.detected_language.value == "en"
    stage_names = [s.stage for s in parsed.stage_timings]
    assert stage_names == ["decode", "inference", "postprocess"]
    assert parsed.degraded.active is False


def test_transcribe_corrupt_file_returns_pipeline_error(client: TestClient) -> None:
    payload = _transcribe_payload(
        audio_format="mp3", uri=_fixture_uri("tone_2s_corrupt.mp3")
    )
    resp = client.post("/v1/transcribe", json=payload)
    assert resp.status_code == 422

    parsed = PipelineError.model_validate(resp.json())
    assert parsed.code == ErrorCode.AUDIO_FETCH_FAILED
    assert parsed.stage == "transcribe.decode"


def test_transcribe_ffmpeg_not_found_returns_clean_pipeline_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FFMPEG_PATH", "this-binary-does-not-exist-anywhere")
    payload = _transcribe_payload(audio_format="ogg_opus", uri=_fixture_uri("tone_2s.opus"))
    resp = client.post("/v1/transcribe", json=payload)
    assert resp.status_code == 422

    parsed = PipelineError.model_validate(resp.json())
    assert parsed.code == ErrorCode.AUDIO_FETCH_FAILED
    assert parsed.stage == "transcribe.decode"
    assert "not found" in parsed.message.lower()
