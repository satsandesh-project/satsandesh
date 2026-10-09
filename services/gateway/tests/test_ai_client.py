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


# --- the transcribe timeout scales with the audio ---------------------------------------
#
# A fixed 120 s cannot fit a decoder whose time scales with how long the note is (and with how much
# text it emits). Measured on a 33.5 s synthetic Telugu note, one at a time: 130.3, 67.0, 114.5, 69.7,
# 75.9 s for the ASR call alone (infra/ai/GATE_WEEK8.md): one call already past 120 s. The floor stays
# AI_TRANSCRIBE_TIMEOUT_S; a note gets `per_audio_s` seconds of decode per second of audio, up to a cap.


def _audio_request(duration_ms: int | None) -> TranscribeRequest:
    return TranscribeRequest(
        audio=AudioRef(uri="file:///data/x.bin", format=AudioFormat.MP3, duration_ms=duration_ms)
    )


def _read_timeout_seen(duration_ms: int | None, **kwargs) -> float:
    reads: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reads.append(request.extensions["timeout"]["read"])
        return httpx.Response(500)

    c = _client(handler, timeouts={AiStage.TRANSCRIBE: 120.0}, **kwargs)
    with pytest.raises(AiCallError):
        c.transcribe(_audio_request(duration_ms))
    return reads[0]


def test_a_long_note_gets_a_timeout_proportional_to_its_length() -> None:
    seen = _read_timeout_seen(
        33_500, transcribe_timeout_per_audio_s=6.0, transcribe_timeout_max_s=600.0
    )
    assert seen == pytest.approx(201.0)


def test_a_short_note_never_gets_less_than_the_floor() -> None:
    seen = _read_timeout_seen(
        5_000, transcribe_timeout_per_audio_s=6.0, transcribe_timeout_max_s=600.0
    )
    assert seen == 120.0


def test_the_timeout_never_exceeds_the_cap_whatever_length_the_client_declared() -> None:
    seen = _read_timeout_seen(
        3_600_000, transcribe_timeout_per_audio_s=6.0, transcribe_timeout_max_s=600.0
    )
    assert seen == 600.0


def test_an_undeclared_length_uses_the_floor() -> None:
    seen = _read_timeout_seen(
        None, transcribe_timeout_per_audio_s=6.0, transcribe_timeout_max_s=600.0
    )
    assert seen == 120.0


def test_per_audio_zero_keeps_the_old_fixed_timeout() -> None:
    assert _read_timeout_seen(33_500, transcribe_timeout_per_audio_s=0.0) == 120.0


def test_a_cap_below_the_floor_does_not_shorten_the_floor() -> None:
    seen = _read_timeout_seen(
        33_500, transcribe_timeout_per_audio_s=6.0, transcribe_timeout_max_s=60.0
    )
    assert seen == 120.0


def test_the_timeout_error_names_the_timeout_that_was_actually_used() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    c = _client(
        handler,
        timeouts={AiStage.TRANSCRIBE: 120.0},
        transcribe_timeout_per_audio_s=6.0,
        transcribe_timeout_max_s=600.0,
    )
    with pytest.raises(AiCallError) as info:
        c.transcribe(_audio_request(33_500))
    assert info.value.kind is AiErrorKind.TIMEOUT
    assert info.value.retryable
    assert "within 201s" in str(info.value)


def test_the_other_stages_keep_their_fixed_timeouts() -> None:
    reads: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reads.append(request.extensions["timeout"]["read"])
        return httpx.Response(500)

    c = _client(
        handler,
        timeouts={AiStage.PIVOT: 22.0},
        transcribe_timeout_per_audio_s=6.0,
        transcribe_timeout_max_s=600.0,
    )
    with pytest.raises(AiCallError):
        c.pivot(_PIVOT)
    assert reads == [22.0]


def test_from_settings_wires_the_scaling(monkeypatch) -> None:
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "AI_TRANSCRIBE_TIMEOUT_S", 100.0)
    monkeypatch.setattr(s, "AI_TRANSCRIBE_TIMEOUT_PER_AUDIO_S", 5.0)
    monkeypatch.setattr(s, "AI_TRANSCRIBE_TIMEOUT_MAX_S", 400.0)

    c = AiClient.from_settings(s)
    try:
        assert c.transcribe_timeout_for(_audio_request(40_000).audio.duration_ms) == 200.0
        assert c.transcribe_timeout_for(10_000_000) == 400.0
    finally:
        c.close()


# --- per-language ASR routing (off by default) ---------------------------------------------
#
# M3 (#98): Telugu and Hindi are expected to go to `speech_indicconformer`, English stays on
# `speech`; IndicConformer has no English and no language auto-detect, so the declared hint decides.
# NOTHING is routed until a deployment sets the map; with the map empty every note goes where it
# always went.

_INDIC = "http://speech-indic:8004"
_BY_LANGUAGE = {"te": _INDIC, "hi": _INDIC}


def _urls_seen(hint, **kwargs) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(500)

    c = _client(handler, **kwargs)
    request = TranscribeRequest(
        audio=AudioRef(uri="file:///data/x.bin", format=AudioFormat.MP3),
        language_hint=hint,
    )
    with pytest.raises(AiCallError):
        c.transcribe(request)
    return seen


def test_a_routed_language_goes_to_its_own_service() -> None:
    for hint in (LanguageCode.TELUGU, LanguageCode.HINDI):
        seen = _urls_seen(hint, transcribe_urls_by_language=_BY_LANGUAGE)
        assert seen == [f"{_INDIC}/v1/transcribe"]


def test_english_and_an_undeclared_language_stay_on_the_default_service() -> None:
    for hint in (LanguageCode.ENGLISH, None):
        seen = _urls_seen(hint, transcribe_urls_by_language=_BY_LANGUAGE)
        assert seen == ["http://ai.test/v1/transcribe"]


def test_with_no_map_every_language_goes_where_it_always_went() -> None:
    for hint in (LanguageCode.TELUGU, LanguageCode.HINDI, LanguageCode.ENGLISH, None):
        assert _urls_seen(hint) == ["http://ai.test/v1/transcribe"]
        assert _urls_seen(hint, transcribe_urls_by_language={}) == ["http://ai.test/v1/transcribe"]


def test_routing_only_moves_the_transcribe_stage() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(500)

    c = _client(handler, transcribe_urls_by_language=_BY_LANGUAGE)
    with pytest.raises(AiCallError):
        c.pivot(_PIVOT)
    assert seen == ["http://ai.test/v1/pivot"]


def test_the_timeout_error_names_the_routed_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    c = _client(handler, transcribe_urls_by_language=_BY_LANGUAGE)
    request = TranscribeRequest(
        audio=AudioRef(uri="file:///x", format=AudioFormat.MP3), language_hint=LanguageCode.HINDI
    )
    with pytest.raises(AiCallError) as info:
        c.transcribe(request)
    assert _INDIC in str(info.value)


def test_from_settings_wires_the_map_and_an_empty_one_means_no_routing(monkeypatch) -> None:
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "AI_TRANSCRIBE_URL", "http://asr:8002")
    monkeypatch.setattr(s, "AI_TRANSCRIBE_URLS_BY_LANGUAGE", {"te": _INDIC})
    c = AiClient.from_settings(s)
    try:
        assert c.transcribe_url_for(LanguageCode.TELUGU) == _INDIC
        assert c.transcribe_url_for(LanguageCode.HINDI) == "http://asr:8002"
        assert c.transcribe_url_for(None) == "http://asr:8002"
    finally:
        c.close()
    monkeypatch.setattr(s, "AI_TRANSCRIBE_URLS_BY_LANGUAGE", {})
    c = AiClient.from_settings(s)
    try:
        assert c.transcribe_url_for(LanguageCode.TELUGU) == "http://asr:8002"
    finally:
        c.close()


def test_the_setting_accepts_json_from_the_environment_and_empty_means_none(monkeypatch) -> None:
    from app.config import Settings

    base = {"DATABASE_URL": "postgresql://x", "JWT_SECRET": "s" * 32}
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "a")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "b")
    monkeypatch.setenv("MEDIA_STORAGE_ROOT", "/tmp/m")
    monkeypatch.setenv("AI_TRANSCRIBE_URLS_BY_LANGUAGE", '{"te": "http://speech-indic:8004"}')
    assert Settings().AI_TRANSCRIBE_URLS_BY_LANGUAGE == {"te": "http://speech-indic:8004"}
    monkeypatch.setenv("AI_TRANSCRIBE_URLS_BY_LANGUAGE", "")
    assert Settings().AI_TRANSCRIBE_URLS_BY_LANGUAGE == {}


def test_a_routed_note_still_gets_the_length_scaled_timeout() -> None:
    """The two features compose: routing picks the URL, the audio's length picks the timeout."""
    reads: list[float] = []
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reads.append(request.extensions["timeout"]["read"])
        urls.append(str(request.url))
        return httpx.Response(500)

    c = _client(
        handler,
        timeouts={AiStage.TRANSCRIBE: 120.0},
        transcribe_timeout_per_audio_s=6.0,
        transcribe_timeout_max_s=600.0,
        transcribe_urls_by_language=_BY_LANGUAGE,
    )
    request = TranscribeRequest(
        audio=AudioRef(uri="file:///x", format=AudioFormat.MP3, duration_ms=33_500),
        language_hint=LanguageCode.TELUGU,
    )
    with pytest.raises(AiCallError):
        c.transcribe(request)
    assert reads == [pytest.approx(201.0)]
    assert urls == [f"{_INDIC}/v1/transcribe"]
