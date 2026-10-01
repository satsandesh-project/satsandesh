"""
Tests for the real MT pivot service under services/ai/mt/.

Mirrors services/ai/render/tests/test_render_app.py's split: the default
suite runs against `stub_mt_client`, a deterministic stand-in for
IndicTrans2 so service logic (routing, passthrough, health, error shapes)
is testable without the indic-en model, torch, or transformers installed
at all -- CI's `pip install -e ".[dev]"` never pulls those in. The real
model is exercised by `real_mt_client`, explicitly marked `integration`
(pyproject.toml's `addopts = "-m 'not integration'"` skips it by default),
so a CI run without the `mt` extras group installed never attempts to
import torch in the first place.
"""

import pytest
import services.ai.mt.app as mt_app
from contracts.ai.pivot import PivotResponse
from fastapi.testclient import TestClient
from services.ai.mt.engine import MtEngine, TranslationResult

_STUB_TRANSLATIONS = {
    "te": "Hello, how are you? (stub)",
    "hi": "Hello, how are you? (stub)",
}


class StubMt:
    model_version = "stub-mt"
    load_duration_ms = 0.0
    warmup_duration_ms = 0.0

    def load(self) -> None:
        pass

    def warm_up(self, text: str, source_language) -> None:
        pass

    def translate(self, text: str, source_language) -> TranslationResult:
        return TranslationResult(
            text=_STUB_TRANSLATIONS[source_language.value],
            preprocess_duration_ms=1.0,
            inference_duration_ms=1.0,
            postprocess_duration_ms=1.0,
        )


def _pivot_payload(text: str, source_language: str) -> dict:
    return {"text": text, "source_language": source_language}


@pytest.fixture(scope="module")
def stub_mt_client():
    original = mt_app.engine
    mt_app.engine = StubMt()
    try:
        with TestClient(mt_app.app) as c:
            yield c
    finally:
        mt_app.engine = original


@pytest.fixture(scope="module")
def real_mt_client():
    mt_app.engine = MtEngine(
        model_name=mt_app.settings.model_name,
        device=mt_app.settings.device,
        num_beams=mt_app.settings.num_beams,
        max_length=mt_app.settings.max_length,
    )
    try:
        with TestClient(mt_app.app) as c:
            yield c
    except (ModuleNotFoundError, OSError) as exc:
        # Defense in depth for someone running `-m integration` locally
        # without the `mt` extras installed, or without network access to
        # download model weights. In CI this path is never reached at all
        # -- the `integration` marker on every test below means pytest
        # deselects them before this fixture is ever instantiated.
        pytest.skip(f"real indic-en model not accessible: {type(exc).__name__}: {exc}")


# ---- service behaviour (stub MT) --------------------------------------------------


def test_health_live_is_always_200(stub_mt_client: TestClient) -> None:
    resp = stub_mt_client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "live"}


def test_health_ready_reflects_load_state(stub_mt_client: TestClient) -> None:
    assert mt_app._ready is True
    ready_resp = stub_mt_client.get("/health/ready")
    assert ready_resp.status_code == 200
    body = ready_resp.json()
    assert body["status"] == "ready"
    assert body["model_version"] == mt_app.engine.model_version
    assert body["load_duration_ms"] is not None
    assert body["warmup_duration_ms"] is not None

    mt_app._ready = False
    try:
        not_ready_resp = stub_mt_client.get("/health/ready")
        assert not_ready_resp.status_code == 503
        assert not_ready_resp.json() == {"status": "not_ready"}
    finally:
        mt_app._ready = True


def test_pivot_telugu_routes_through_engine_and_returns_result(
    stub_mt_client: TestClient,
) -> None:
    telugu_text = "నమస్కారం, మీరు ఎలా ఉన్నారు?"  # "Hello, how are you?"
    resp = stub_mt_client.post("/v1/pivot", json=_pivot_payload(telugu_text, "te"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.source_language.value == "te"
    assert parsed.model_version == mt_app.engine.model_version
    assert parsed.duration_ms >= 0
    assert parsed.degraded.active is False
    assert parsed.pivot_text == _STUB_TRANSLATIONS["te"]


def test_pivot_hindi_routes_through_engine_and_returns_result(
    stub_mt_client: TestClient,
) -> None:
    hindi_text = "नमस्ते, आप कैसे हैं?"  # "Hello, how are you?"
    resp = stub_mt_client.post("/v1/pivot", json=_pivot_payload(hindi_text, "hi"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.source_language.value == "hi"
    assert parsed.model_version == mt_app.engine.model_version
    assert parsed.pivot_text == _STUB_TRANSLATIONS["hi"]


def test_pivot_english_source_is_passthrough(stub_mt_client: TestClient) -> None:
    english_text = "Good morning, may your day be peaceful."
    resp = stub_mt_client.post("/v1/pivot", json=_pivot_payload(english_text, "en"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.pivot_text == english_text  # assumption: en source echoes input
    assert parsed.source_language.value == "en"
    assert parsed.model_version == mt_app._PASSTHROUGH_MODEL_VERSION


def test_pivot_empty_text_is_empty_passthrough_not_an_error(stub_mt_client: TestClient) -> None:
    resp = stub_mt_client.post("/v1/pivot", json=_pivot_payload("   ", "te"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.pivot_text == ""
    assert parsed.model_version == mt_app._PASSTHROUGH_MODEL_VERSION
    assert parsed.degraded.active is False


def test_pivot_unsupported_language_is_rejected_by_pydantic_before_reaching_handler(
    stub_mt_client: TestClient,
) -> None:
    # LanguageCode (contracts/ai/language.py) is a closed enum (en/hi/te only),
    # so an out-of-enum source_language never reaches services/ai/mt/app.py's
    # own logic -- FastAPI/Pydantic rejects it at the request-parsing boundary
    # with its own 422 body shape, not our PipelineError. Same finding as
    # services/ai/speech/'s earlier ASR audit: a dedicated PipelineError test
    # for "unsupported language" via a real request is not reachable here.
    resp = stub_mt_client.post("/v1/pivot", json=_pivot_payload("some text", "ta"))
    assert resp.status_code == 422
    assert "source_language" in resp.text


# ---- real MT model (requires torch/transformers/IndicTransToolkit; `-m integration`) ---


@pytest.mark.integration
def test_real_pivot_telugu_returns_genuinely_translated_text(
    real_mt_client: TestClient,
) -> None:
    telugu_text = "నమస్కారం, మీరు ఎలా ఉన్నారు?"  # "Hello, how are you?"
    resp = real_mt_client.post("/v1/pivot", json=_pivot_payload(telugu_text, "te"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.source_language.value == "te"
    assert parsed.model_version == mt_app.engine.model_version
    assert parsed.duration_ms >= 0
    assert parsed.degraded.active is False
    assert parsed.pivot_text.strip() != ""
    assert parsed.pivot_text != telugu_text  # genuinely translated, not an echo
    assert (
        parsed.pivot_text.encode("ascii", errors="ignore").decode() == parsed.pivot_text
    )  # looks like English


@pytest.mark.integration
def test_real_pivot_hindi_returns_genuinely_translated_text(real_mt_client: TestClient) -> None:
    hindi_text = "नमस्ते, आप कैसे हैं?"  # "Hello, how are you?"
    resp = real_mt_client.post("/v1/pivot", json=_pivot_payload(hindi_text, "hi"))
    assert resp.status_code == 200

    parsed = PivotResponse.model_validate(resp.json())
    assert parsed.source_language.value == "hi"
    assert parsed.pivot_text.strip() != ""
    assert parsed.pivot_text != hindi_text
