"""
Tests for the real MT pivot service under services/ai/mt/. This loads the
actual IndicTrans2 indic-en model once per test session -- unlike
services/ai/mock/app.py, these tests take real seconds, not milliseconds,
because there is no mock here to fall back to.
"""

import pytest
import services.ai.mt.app as mt_app
from contracts.ai.pivot import PivotResponse
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    with TestClient(mt_app.app) as c:
        yield c


def _pivot_payload(text: str, source_language: str) -> dict:
    return {"text": text, "source_language": source_language}


def test_health_live_is_always_200(client: TestClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "live"}


def test_health_ready_reflects_load_state(client: TestClient) -> None:
    # Same approach as services/ai/speech/tests/test_speech_app.py: `client`
    # is module-scoped and entered as a context manager, so by the time any
    # test receives it, lifespan startup (model load + warm-up) has already
    # completed. There is no way to observe a genuinely cold process through
    # this same client without reloading the model per test, so this flips
    # the module-level `_ready` flag directly to simulate the not-ready
    # window, exercises the endpoint, then restores it.
    assert mt_app._ready is True
    ready_resp = client.get("/health/ready")
    assert ready_resp.status_code == 200
    body = ready_resp.json()
    assert body["status"] == "ready"
    assert body["model_version"] == mt_app.engine.model_version
    assert body["load_duration_ms"] is not None
    assert body["warmup_duration_ms"] is not None

    mt_app._ready = False
    try:
        not_ready_resp = client.get("/health/ready")
        assert not_ready_resp.status_code == 503
        assert not_ready_resp.json() == {"status": "not_ready"}
    finally:
        mt_app._ready = True


def test_pivot_telugu_returns_genuinely_translated_text(client: TestClient) -> None:
    telugu_text = "నమస్కారం, మీరు ఎలా ఉన్నారు?"  # "Hello, how are you?"
    resp = client.post("/v1/pivot", json=_pivot_payload(telugu_text, "te"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.source_language.value == "te"
    assert parsed.model_version == mt_app.engine.model_version
    assert parsed.duration_ms >= 0
    assert parsed.degraded.active is False
    assert parsed.pivot_text.strip() != ""
    assert parsed.pivot_text != telugu_text  # genuinely translated, not an echo
    assert parsed.pivot_text.encode("ascii", errors="ignore").decode() == parsed.pivot_text  # looks like English


def test_pivot_hindi_returns_genuinely_translated_text(client: TestClient) -> None:
    hindi_text = "नमस्ते, आप कैसे हैं?"  # "Hello, how are you?"
    resp = client.post("/v1/pivot", json=_pivot_payload(hindi_text, "hi"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.source_language.value == "hi"
    assert parsed.model_version == mt_app.engine.model_version
    assert parsed.pivot_text.strip() != ""
    assert parsed.pivot_text != hindi_text


def test_pivot_english_source_is_passthrough(client: TestClient) -> None:
    english_text = "Good morning, may your day be peaceful."
    resp = client.post("/v1/pivot", json=_pivot_payload(english_text, "en"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.pivot_text == english_text  # assumption: en source echoes input
    assert parsed.source_language.value == "en"
    assert parsed.model_version == mt_app._PASSTHROUGH_MODEL_VERSION


def test_pivot_empty_text_is_empty_passthrough_not_an_error(client: TestClient) -> None:
    resp = client.post("/v1/pivot", json=_pivot_payload("   ", "te"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.pivot_text == ""
    assert parsed.model_version == mt_app._PASSTHROUGH_MODEL_VERSION
    assert parsed.degraded.active is False


def test_pivot_unsupported_language_is_rejected_by_pydantic_before_reaching_handler(
    client: TestClient,
) -> None:
    # LanguageCode (contracts/ai/language.py) is a closed enum (en/hi/te only),
    # so an out-of-enum source_language never reaches services/ai/mt/app.py's
    # own logic -- FastAPI/Pydantic rejects it at the request-parsing boundary
    # with its own 422 body shape, not our PipelineError. Same finding as
    # services/ai/speech/'s earlier ASR audit: a dedicated PipelineError test
    # for "unsupported language" via a real request is not reachable here.
    resp = client.post("/v1/pivot", json=_pivot_payload("some text", "ta"))
    assert resp.status_code == 422
    assert "source_language" in resp.text
