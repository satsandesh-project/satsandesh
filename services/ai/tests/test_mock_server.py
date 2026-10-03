"""
Mock server tests — prioritized: the other three students build against
services/ai/mock/app.py from Week 2, so it must be runnable and validated first.
"""

import time

import pytest
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.moderation import ModerationDecision
from contracts.ai.pivot import PivotResponse
from contracts.ai.render import RenderResponse
from contracts.ai.transcribe import TranscribeResponse
from fastapi.testclient import TestClient
from services.ai.mock.app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_transcribe_endpoint_returns_a_valid_transcribe_response(client: TestClient) -> None:
    resp = client.post(
        "/v1/transcribe",
        json={"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}},
    )
    assert resp.status_code == 200
    TranscribeResponse.model_validate(resp.json())


def test_pivot_endpoint_returns_a_valid_pivot_response(client: TestClient) -> None:
    resp = client.post("/v1/pivot", json={"text": "నమస్తే", "source_language": "te"})
    assert resp.status_code == 200
    PivotResponse.model_validate(resp.json())


def test_render_endpoint_fans_out_to_every_target_language(client: TestClient) -> None:
    resp = client.post(
        "/v1/render",
        json={"pivot_text": "Good morning", "target_languages": ["hi", "te"]},
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    assert {r.language.value for r in parsed.results} == {"hi", "te"}


def test_render_endpoint_dedupes_target_languages(client: TestClient) -> None:
    resp = client.post(
        "/v1/render",
        json={"pivot_text": "Good morning", "target_languages": ["hi", "hi", "te"]},
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    assert len(parsed.results) == 2


def test_moderate_endpoint_returns_a_valid_moderation_decision(client: TestClient) -> None:
    resp = client.post("/v1/moderate", json={"text": "Just checking satsang timing"})
    assert resp.status_code == 200
    ModerationDecision.model_validate(resp.json())


def test_mock_server_respects_configured_artificial_latency(client: TestClient) -> None:
    started = time.monotonic()
    resp = client.post(
        "/v1/pivot",
        json={"text": "hello", "source_language": "en"},
        headers={"X-Mock-Latency-Ms": "150"},
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    assert resp.status_code == 200
    assert elapsed_ms >= 150


def test_mock_server_rejects_malformed_request_with_422(client: TestClient) -> None:
    resp = client.post("/v1/pivot", json={"text": "hello", "source_language": "not-a-lang"})
    assert resp.status_code == 422


def test_transcribe_response_reports_degraded_mode_shape(client: TestClient) -> None:
    resp = client.post(
        "/v1/transcribe",
        json={"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}},
    )
    body = resp.json()
    assert "degraded" in body
    assert "active" in body["degraded"]


def test_health_live_is_always_200(client: TestClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "live"}


def test_health_ready_is_always_200_with_no_warmup(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert "model_version" in body
    assert body["load_duration_ms"] is not None
    assert body["warmup_duration_ms"] is not None


def test_valid_x_mock_error_header_produces_a_pipeline_error(client: TestClient) -> None:
    resp = client.post(
        "/v1/transcribe",
        json={"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}},
        headers={"X-Mock-Error": "AUDIO_FETCH_FAILED"},
    )
    assert resp.status_code == 422
    parsed = PipelineError.model_validate(resp.json())
    assert parsed.code == ErrorCode.AUDIO_FETCH_FAILED


def test_invalid_x_mock_error_header_produces_a_clean_400(client: TestClient) -> None:
    resp = client.post(
        "/v1/transcribe",
        json={"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}},
        headers={"X-Mock-Error": "NOT_A_REAL_CODE"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "NOT_A_REAL_CODE" in body["message"]
    assert "AUDIO_FETCH_FAILED" in body["valid_values"] or "AUDIO_FETCH_FAILED" in body["message"]


def test_x_mock_error_header_works_on_every_endpoint(client: TestClient) -> None:
    cases = [
        ("/v1/transcribe", {"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}}),
        ("/v1/pivot", {"text": "hello", "source_language": "en"}),
        ("/v1/render", {"pivot_text": "hi", "target_languages": ["hi"]}),
        ("/v1/moderate", {"text": "hello"}),
    ]
    for path, payload in cases:
        resp = client.post(path, json=payload, headers={"X-Mock-Error": "TIMEOUT"})
        assert resp.status_code == 422, path
        parsed = PipelineError.model_validate(resp.json())
        assert parsed.code == ErrorCode.TIMEOUT


def test_x_mock_degraded_header_marks_at_least_one_render_result_degraded(
    client: TestClient,
) -> None:
    resp = client.post(
        "/v1/render",
        json={"pivot_text": "Good morning", "target_languages": ["hi", "te"]},
        headers={"X-Mock-Degraded": "true"},
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    assert any(r.degraded.active for r in parsed.results)
    degraded_result = next(r for r in parsed.results if r.degraded.active)
    assert degraded_result.degraded.detail


def test_render_without_x_mock_degraded_header_stays_all_ok(client: TestClient) -> None:
    resp = client.post(
        "/v1/render",
        json={"pivot_text": "Good morning", "target_languages": ["hi", "te"]},
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    assert all(not r.degraded.active for r in parsed.results)


def test_transcribe_stage_timings_show_inference_dominating(client: TestClient) -> None:
    resp = client.post(
        "/v1/transcribe",
        json={"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}},
        headers={"X-Mock-Latency-Ms": "100"},
    )
    parsed = TranscribeResponse.model_validate(resp.json())
    timings = {s.stage: s.duration_ms for s in parsed.stage_timings}
    assert timings["inference"] > timings["preprocess"] + timings["postprocess"]
