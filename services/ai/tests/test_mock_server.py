"""
Mock server tests — prioritized: the other three students build against
services/ai/mock/app.py from Week 2, so it must be runnable and validated first.
"""

import time

import pytest
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
