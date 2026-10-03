"""Week 7 Phase 2: the gateway's client for the four AI services.

What the real services actually do on failure (read from their code, not
assumed): none registers an exception handler, so a schema-invalid body gets
FastAPI's default 422 `{"detail": [...]}` (not a PipelineError), an
unexpected exception is a bare 500, "not ready" is a 503 PipelineError, and
`degraded` is a field on a *successful* 200. The mock injects every
PipelineError code as a 422 instead. The client has to turn all of that into
one question the job worker can act on: retry, or give up.

No database needed -- everything here is HTTP shaped.
"""

import json

import httpx
import pytest
from contracts.ai.common import AudioFormat, AudioRef, DegradedReason
from contracts.ai.errors import ErrorCode
from contracts.ai.language import LanguageCode
from contracts.ai.moderation import ModerationAction, ModerationRequest
from contracts.ai.pivot import PivotRequest
from contracts.ai.render import RenderRequest
from contracts.ai.transcribe import TranscribeRequest
from fastapi.testclient import TestClient

from app.ai_client import AiCallError, AiClient, AiErrorKind, AiStage

_TRANSCRIBE = TranscribeRequest(audio=AudioRef(uri="file:///data/x.bin", format=AudioFormat.MP3))
_PIVOT = PivotRequest(text="నమస్కారం", source_language=LanguageCode.TELUGU)
_MODERATE = ModerationRequest(text="Good morning")
_RENDER = RenderRequest(pivot_text="Good morning", target_languages=[LanguageCode.HINDI])


def _client(handler, **kwargs) -> AiClient:
    urls = {stage: "http://ai.test" for stage in AiStage}
    return AiClient(
        urls=urls,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _error_body(code: ErrorCode, message="boom") -> dict:
    return {"contract_version": "0.1.0", "code": code.value, "message": message, "stage": "x"}


def _respond(status: int, body=None, *, text: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=body)

    return handler


# --- happy path against the REAL mock: client and contract agree ---------------


@pytest.fixture
def real_mock_client() -> AiClient:
    from services.ai.mock.app import app as ai_mock_app

    with TestClient(ai_mock_app) as http:
        urls = {stage: "http://testserver" for stage in AiStage}
        yield AiClient(urls=urls, http=http)


def test_all_four_stages_round_trip_through_the_real_mock(real_mock_client) -> None:
    c = real_mock_client
    transcript = c.transcribe(_TRANSCRIBE)
    assert transcript.text
    pivot = c.pivot(_PIVOT)
    assert pivot.pivot_text
    decision = c.moderate(_MODERATE)
    assert decision.action is ModerationAction.ALLOW
    rendered = c.render(_RENDER)
    assert [r.language for r in rendered.results] == [LanguageCode.HINDI]


# --- request shape --------------------------------------------------------------


def test_each_stage_posts_the_contract_json_to_its_own_path_and_base_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500)

    urls = {
        AiStage.TRANSCRIBE: "http://asr:8002",
        AiStage.PIVOT: "http://mt:8004/",  # a trailing slash must not double up
        AiStage.MODERATE: "http://mod:8003",
        AiStage.RENDER: "http://render:8005",
    }
    c = AiClient(urls=urls, http=httpx.Client(transport=httpx.MockTransport(handler)))
    for call, req in [
        (c.transcribe, _TRANSCRIBE),
        (c.pivot, _PIVOT),
        (c.moderate, _MODERATE),
        (c.render, _RENDER),
    ]:
        with pytest.raises(AiCallError):
            call(req)

    assert [str(r.url) for r in seen] == [
        "http://asr:8002/v1/transcribe",
        "http://mt:8004/v1/pivot",
        "http://mod:8003/v1/moderate",
        "http://render:8005/v1/render",
    ]
    assert json.loads(seen[0].content) == _TRANSCRIBE.model_dump(mode="json")
    assert json.loads(seen[1].content)["source_language"] == "te"


def test_each_stage_uses_its_own_timeout() -> None:
    reads: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reads.append(request.extensions["timeout"]["read"])
        return httpx.Response(500)

    timeouts = {
        AiStage.TRANSCRIBE: 111.0,
        AiStage.PIVOT: 22.0,
        AiStage.MODERATE: 33.0,
        AiStage.RENDER: 44.0,
    }
    c = _client(handler, timeouts=timeouts)
    for call, req in [
        (c.transcribe, _TRANSCRIBE),
        (c.pivot, _PIVOT),
        (c.moderate, _MODERATE),
        (c.render, _RENDER),
    ]:
        with pytest.raises(AiCallError):
            call(req)
    assert reads == [111.0, 22.0, 33.0, 44.0]


# --- failure classification: retry or give up ---------------------------------------


def _expect_error(call, req, handler) -> AiCallError:
    c = _client(handler)
    with pytest.raises(AiCallError) as info:
        getattr(c, call)(req)
    return info.value


def test_an_unreachable_service_is_retryable() -> None:
    def refuse(request):
        raise httpx.ConnectError("connection refused")

    err = _expect_error("transcribe", _TRANSCRIBE, refuse)
    assert err.retryable and err.kind is AiErrorKind.UNREACHABLE
    assert err.stage is AiStage.TRANSCRIBE


def test_a_timeout_is_retryable() -> None:
    def slow(request):
        raise httpx.ReadTimeout("too slow")

    err = _expect_error("render", _RENDER, slow)
    assert err.retryable and err.kind is AiErrorKind.TIMEOUT


def test_not_ready_503_is_retryable_and_carries_the_code() -> None:
    err = _expect_error(
        "pivot", _PIVOT, _respond(503, _error_body(ErrorCode.MODEL_LOAD_FAILED, "loading"))
    )
    assert err.retryable and err.kind is AiErrorKind.NOT_READY
    assert err.code is ErrorCode.MODEL_LOAD_FAILED and err.status_code == 503


@pytest.mark.parametrize("code", [ErrorCode.TIMEOUT, ErrorCode.OUT_OF_MEMORY])
def test_transient_codes_are_retryable_even_on_a_422(code) -> None:
    # The mock injects every code as a 422; a real service would not.
    err = _expect_error("transcribe", _TRANSCRIBE, _respond(422, _error_body(code)))
    assert err.retryable and err.code is code


@pytest.mark.parametrize(
    "code", [ErrorCode.AUDIO_FETCH_FAILED, ErrorCode.UNSUPPORTED_LANGUAGE, ErrorCode.INTERNAL_ERROR]
)
def test_a_422_pipeline_error_about_this_request_is_permanent(code) -> None:
    err = _expect_error("transcribe", _TRANSCRIBE, _respond(422, _error_body(code)))
    assert not err.retryable
    assert err.kind is AiErrorKind.REJECTED and err.code is code


def test_fastapis_default_422_is_permanent_and_is_not_mistaken_for_a_pipeline_error() -> None:
    body = {"detail": [{"loc": ["body", "audio", "format"], "msg": "bad", "type": "enum"}]}
    err = _expect_error("transcribe", _TRANSCRIBE, _respond(422, body))
    assert not err.retryable and err.kind is AiErrorKind.REJECTED
    assert err.code is None


def test_a_404_is_permanent() -> None:
    # What the Week-1 ai-services health-check stub returns for every /v1/*.
    err = _expect_error("transcribe", _TRANSCRIBE, _respond(404, {"detail": "Not Found"}))
    assert not err.retryable and err.status_code == 404


@pytest.mark.parametrize("status", [408, 429])
def test_slow_down_and_timed_out_are_retryable(status) -> None:
    err = _expect_error("moderate", _MODERATE, _respond(status, {}))
    assert err.retryable


@pytest.mark.parametrize("status", [500, 502, 504])
def test_a_5xx_with_no_pipeline_error_body_is_retryable(status) -> None:
    # An unhandled exception inside a service is a bare 500 with a text body.
    err = _expect_error("render", _RENDER, _respond(status, text="Internal Server Error"))
    assert err.retryable and err.kind is AiErrorKind.SERVER_ERROR


def test_a_200_that_is_not_json_is_a_permanent_contract_violation() -> None:
    err = _expect_error("pivot", _PIVOT, _respond(200, text="<html>proxy error</html>"))
    assert not err.retryable and err.kind is AiErrorKind.BAD_RESPONSE


def test_a_200_that_breaks_the_contract_is_a_permanent_contract_violation() -> None:
    err = _expect_error("pivot", _PIVOT, _respond(200, {"pivot_text": "x"}))
    assert not err.retryable and err.kind is AiErrorKind.BAD_RESPONSE


# --- degraded is data, not an error ---------------------------------------------------


def test_a_degraded_moderation_hold_is_returned_not_raised() -> None:
    """Moderation fails CLOSED: on a model error it answers 200 with HOLD and
    degraded set. That is a decision the orchestrator must see, not a
    transport failure the client should hide behind an exception."""
    body = {
        "contract_version": "0.1.0",
        "label": "D_DISPUTATIONAL",
        "confidence": 0.0,
        "action": "HOLD",
        "rationale": "Classifier produced no usable verdict; held for human review.",
        "nudge_text": None,
        "nudge_language": None,
        "policy_version": "policy@2026-09-22",
        "model_version": "stub",
        "degraded": {"active": True, "reason": "model_fallback", "detail": "timeout after 20s"},
    }
    decision = _client(_respond(200, body)).moderate(_MODERATE)
    assert decision.action is ModerationAction.HOLD
    assert decision.degraded.active
    assert decision.degraded.reason is DegradedReason.MODEL_FALLBACK


def test_a_partially_degraded_render_is_returned_with_its_per_language_flags() -> None:
    body = {
        "contract_version": "0.1.0",
        "results": [
            {
                "language": "hi",
                "text": "नमस्ते",
                "audio": {"uri": "", "format": "wav_pcm16", "duration_ms": 0},
                "model_version_translate": "m",
                "model_version_tts": "t",
                "duration_ms": 5.0,
                "degraded": {"active": True, "reason": "tts_skipped", "detail": "TTS failed"},
            }
        ],
        "degraded": {"active": True, "reason": "tts_skipped", "detail": "x"},
    }
    rendered = _client(_respond(200, body)).render(_RENDER)
    assert rendered.results[0].degraded.reason is DegradedReason.TTS_SKIPPED


# --- construction from settings --------------------------------------------------------


def test_from_settings_falls_back_to_the_shared_url_and_honours_per_stage_overrides(
    monkeypatch,
) -> None:
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "AI_SERVICE_URL", "http://shared:8001")
    monkeypatch.setattr(s, "AI_TRANSCRIBE_URL", "http://asr:8002")
    monkeypatch.setattr(s, "AI_PIVOT_URL", None)
    monkeypatch.setattr(s, "AI_MODERATION_URL", None)
    monkeypatch.setattr(s, "AI_RENDER_URL", "http://render:8005")
    monkeypatch.setattr(s, "AI_TRANSCRIBE_TIMEOUT_S", 99.0)

    c = AiClient.from_settings(s)
    try:
        assert c.url_for(AiStage.TRANSCRIBE) == "http://asr:8002"
        assert c.url_for(AiStage.PIVOT) == "http://shared:8001"
        assert c.url_for(AiStage.MODERATE) == "http://shared:8001"
        assert c.url_for(AiStage.RENDER) == "http://render:8005"
        assert c.timeout_for(AiStage.TRANSCRIBE) == 99.0
    finally:
        c.close()


def test_default_timeouts_are_longer_than_the_services_own_limits() -> None:
    # moderation's own inference timeout is 20s (MOD_TIMEOUT_S); a client that
    # gave up first would turn the service's fail-closed HOLD into a retry.
    c = _client(_respond(200, {}))
    assert c.timeout_for(AiStage.MODERATE) > 20
    # CPU ASR on a cold model is slow (the demo console allows 120s).
    assert c.timeout_for(AiStage.TRANSCRIBE) >= 120
