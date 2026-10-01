"""
Fast unit tests for services/ai/speech_indicconformer/ -- a stub engine stands in for the
real model, so no model download, no HF_TOKEN and no inference are needed.
"""

from __future__ import annotations

import pytest
import services.ai.speech_indicconformer.app as ic_app
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.transcribe import TranscribeResponse
from fastapi.testclient import TestClient
from services.ai.speech_indicconformer.engine import (
    TranscriptionResult,
    UnsupportedLanguageError,
)
from services.ai.speech_indicconformer.settings import Settings, SettingsError

_FIXTURES = ic_app._WARMUP_AUDIO_PATH.parent
TONE_URI = "file:///" + str(ic_app._WARMUP_AUDIO_PATH).replace("\\", "/")


def _uri(name: str) -> str:
    return "file:///" + str(_FIXTURES / name).replace("\\", "/")


class StubEngine:
    model_version = "stub-indicconformer-ctc"
    decode_mode = "ctc"
    load_duration_ms = 1.0
    warmup_duration_ms = 1.0

    def transcribe(self, audio, language, decode_mode=None) -> TranscriptionResult:
        if language not in ("hi", "te"):
            raise UnsupportedLanguageError(f"language {language!r} is not supported")
        return TranscriptionResult(
            text="నమస్కారం",
            language=language,
            decode_mode="ctc",
            inference_duration_ms=5.0,
            postprocess_duration_ms=0.1,
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # No `with` block: lifespan (the real model load) is intentionally NOT run.
    monkeypatch.setattr(ic_app, "engine", StubEngine())
    monkeypatch.setattr(ic_app, "_ready", True)
    return TestClient(ic_app.app)


def _payload(fmt: str = "wav_pcm16", hint: str | None = "te", uri: str | None = None) -> dict:
    payload: dict = {
        "audio": {
            "uri": uri or TONE_URI,
            "format": fmt,
            "duration_ms": 2000,
            "sample_rate_hz": 16000,
        }
    }
    if hint is not None:
        payload["language_hint"] = hint
    return payload


# ---- settings -----------------------------------------------------------------------


def test_settings_fail_fast_without_hf_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(SettingsError, match="HF_TOKEN"):
        Settings.from_env()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    for var in ("DECODE_MODE", "ASR_MODEL_NAME", "ASR_PORT"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert s.decode_mode == "ctc"
    assert s.model_name == "ai4bharat/indic-conformer-600m-multilingual"
    assert s.port == 8004


def test_settings_decode_mode_can_be_flipped_and_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    monkeypatch.setenv("DECODE_MODE", "RNNT")
    assert Settings.from_env().decode_mode == "rnnt"
    monkeypatch.setenv("DECODE_MODE", "greedy")
    with pytest.raises(SettingsError, match="DECODE_MODE"):
        Settings.from_env()


def test_settings_rejects_bad_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    monkeypatch.setenv("ASR_PORT", "70000")
    with pytest.raises(SettingsError, match="ASR_PORT"):
        Settings.from_env()


# ---- health -------------------------------------------------------------------------


def test_health_live(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "live"}


def test_health_ready_and_not_ready(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["model_version"] == "stub-indicconformer-ctc"
    monkeypatch.setattr(ic_app, "_ready", False)
    assert client.get("/health/ready").status_code == 503


# ---- contract shape / formats / errors -----------------------------------------------


def test_transcribe_wav_response_matches_contract(client: TestClient) -> None:
    resp = client.post("/v1/transcribe", json=_payload())
    assert resp.status_code == 200
    parsed = TranscribeResponse.model_validate(resp.json())
    assert parsed.text == "నమస్కారం"
    assert parsed.detected_language.value == "te"
    assert [s.stage for s in parsed.stage_timings] == ["decode", "inference", "postprocess"]
    assert parsed.degraded.active is False


@pytest.mark.parametrize(("fmt", "fixture"), [("ogg_opus", "tone_2s.opus"), ("mp3", "tone_2s.mp3")])
def test_transcribe_ffmpeg_formats(client: TestClient, fmt: str, fixture: str) -> None:
    resp = client.post("/v1/transcribe", json=_payload(fmt=fmt, uri=_uri(fixture)))
    assert resp.status_code == 200
    TranscribeResponse.model_validate(resp.json())


def test_transcribe_corrupt_file_is_pipeline_error(client: TestClient) -> None:
    resp = client.post("/v1/transcribe", json=_payload(fmt="mp3", uri=_uri("tone_2s_corrupt.mp3")))
    assert resp.status_code == 422
    err = PipelineError.model_validate(resp.json())
    assert err.code == ErrorCode.AUDIO_FETCH_FAILED
    assert err.stage == "transcribe.decode"


def test_transcribe_missing_file_is_pipeline_error(client: TestClient) -> None:
    resp = client.post("/v1/transcribe", json=_payload(uri="file:///C:/does/not/exist.wav"))
    assert resp.status_code == 422
    assert PipelineError.model_validate(resp.json()).code == ErrorCode.AUDIO_FETCH_FAILED


@pytest.mark.parametrize("hint", [None, "en"])
def test_missing_or_english_hint_is_unsupported_language(
    client: TestClient, hint: str | None
) -> None:
    resp = client.post("/v1/transcribe", json=_payload(hint=hint))
    assert resp.status_code == 422
    assert PipelineError.model_validate(resp.json()).code == ErrorCode.UNSUPPORTED_LANGUAGE


def test_not_ready_returns_503_pipeline_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ic_app, "_ready", False)
    resp = client.post("/v1/transcribe", json=_payload())
    assert resp.status_code == 503
    assert PipelineError.model_validate(resp.json()).code == ErrorCode.MODEL_LOAD_FAILED
