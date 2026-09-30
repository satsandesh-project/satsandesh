"""
Tests for the real render service under services/ai/render/.

Piper TTS is always REAL here (two voices, ungated, downloaded on first run). The
MT half comes in two flavours:
  * tests using `stub_mt_client`: a deterministic stand-in for IndicTrans2, so the
    service logic (fan-out, degraded mode, audio files, health) is testable without
    the gated en-indic model;
  * tests using `real_mt_client`: the real IndicTrans2 en-indic model, skipped with an
    explicit reason if the HF account has not accepted that repo's gate.

Requires HF_TOKEN in the environment (settings fail fast without it); the stub tests
never send it anywhere.
"""

import os
import wave
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pytest

os.environ.setdefault("HF_TOKEN", "test-token-unused-by-stub-tests")

import services.ai.render.app as render_app
from contracts.ai.common import DegradedReason
from contracts.ai.language import LanguageCode
from contracts.ai.render import RenderResponse
from fastapi.testclient import TestClient
from services.ai.render.engine import MtEngine, TranslationResult, flores_code_for
from services.ai.render.settings import Settings, SettingsError

_STUB_TRANSLATIONS = {
    LanguageCode.HINDI: "सुप्रभात, आपका दिन शांतिपूर्ण हो।",
    LanguageCode.TELUGU: "శుభోదయం, మీ రోజు ప్రశాంతంగా ఉండాలి.",
}


class StubMt:
    model_version = "stub-mt"
    load_duration_ms = 0.0
    warmup_duration_ms = 0.0

    def load(self) -> None:
        pass

    def warm_up(self, text: str, target_language: LanguageCode) -> None:
        pass

    def translate(self, text: str, target_language: LanguageCode) -> TranslationResult:
        return TranslationResult(text=_STUB_TRANSLATIONS[target_language], duration_ms=1.0)


def _audio_path(uri: str) -> Path:
    return Path(url2pathname(unquote(urlparse(uri).path)))


def _assert_playable_wav(uri: str, expected_duration_ms: int | None) -> None:
    path = _audio_path(uri)
    assert path.is_file(), path
    with wave.open(str(path), "rb") as wav:  # raises wave.Error if not real WAV
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 22050
        seconds = wav.getnframes() / wav.getframerate()
        assert seconds > 0.5
        frames = wav.readframes(wav.getnframes())
    assert any(frames), "audio is all zero bytes (silence)"
    if expected_duration_ms is not None:
        assert abs(seconds * 1000 - expected_duration_ms) <= 1


@pytest.fixture(scope="module")
def stub_mt_client(tmp_path_factory):
    original = render_app.mt_engine
    render_app.mt_engine = StubMt()
    render_app.settings = Settings(
        **{**render_app.settings.__dict__, "output_dir": tmp_path_factory.mktemp("render_out")}
    )
    try:
        with TestClient(render_app.app) as c:
            yield c
    finally:
        render_app.mt_engine = original


@pytest.fixture(scope="module")
def real_mt_client(tmp_path_factory):
    from huggingface_hub.utils import GatedRepoError, HfHubHTTPError

    render_app.settings = Settings(
        **{**render_app.settings.__dict__, "output_dir": tmp_path_factory.mktemp("render_out_real")}
    )
    render_app.mt_engine = MtEngine(
        model_name=render_app.settings.mt_model_name,
        device=render_app.settings.mt_device,
        num_beams=render_app.settings.num_beams,
        max_length=render_app.settings.max_length,
        hf_token=render_app.settings.hf_token,
    )
    try:
        with TestClient(render_app.app) as c:
            yield c
    except (GatedRepoError, HfHubHTTPError, OSError) as exc:
        pytest.skip(f"real en-indic model not accessible with this HF_TOKEN: {type(exc).__name__}")


# ---- settings -------------------------------------------------------------------


def test_settings_fail_fast_without_hf_token(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(SettingsError, match="HF_TOKEN"):
        Settings.from_env()


def test_settings_defaults_and_validation(monkeypatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "x")
    s = Settings.from_env()
    assert s.mt_model_name == "ai4bharat/indictrans2-en-indic-dist-200M"
    assert (s.voice_te, s.voice_hi) == ("te_IN-maya-medium", "hi_IN-pratham-medium")
    assert s.port == 8005
    monkeypatch.setenv("RENDER_PORT", "abc")
    with pytest.raises(SettingsError):
        Settings.from_env()


def test_flores_mapping_is_target_direction() -> None:
    assert flores_code_for(LanguageCode.HINDI) == "hin_Deva"
    assert flores_code_for(LanguageCode.TELUGU) == "tel_Telu"


# ---- service behaviour (stub MT, real Piper) --------------------------------------


def test_health(stub_mt_client: TestClient) -> None:
    assert stub_mt_client.get("/health/live").json() == {"status": "live"}
    body = stub_mt_client.get("/health/ready").json()
    assert body["status"] == "ready"
    assert set(body["tts_voices"]) == {"hi", "te"}
    render_app._ready = False
    try:
        assert stub_mt_client.get("/health/ready").status_code == 503
        assert (
            stub_mt_client.post(
                "/v1/render", json={"pivot_text": "hi", "target_languages": ["hi"]}
            ).status_code
            == 503
        )
    finally:
        render_app._ready = True


def test_render_hi_and_te_returns_two_real_results(stub_mt_client: TestClient) -> None:
    resp = stub_mt_client.post(
        "/v1/render",
        json={
            "pivot_text": "Good morning, may your day be peaceful.",
            "target_languages": ["hi", "te"],
        },
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    assert [r.language.value for r in parsed.results] == ["hi", "te"]
    assert parsed.degraded.active is False
    for r in parsed.results:
        assert r.text.strip() != ""
        assert r.degraded.active is False
        assert r.audio.format.value == "wav_pcm16"
        assert r.audio.sample_rate_hz == 22050
        assert r.model_version_tts.startswith("piper:")
        assert r.duration_ms > 0
        _assert_playable_wav(r.audio.uri, r.audio.duration_ms)
    assert parsed.results[0].model_version_tts.startswith("piper:hi_IN-pratham-medium")
    assert parsed.results[1].model_version_tts.startswith("piper:te_IN-maya-medium")
    assert _audio_path(parsed.results[0].audio.uri) != _audio_path(parsed.results[1].audio.uri)


def test_render_dedupes_languages(stub_mt_client: TestClient) -> None:
    resp = stub_mt_client.post(
        "/v1/render", json={"pivot_text": "Good morning", "target_languages": ["te", "te"]}
    )
    assert [r["language"] for r in resp.json()["results"]] == ["te"]


def test_render_empty_target_languages_is_rejected_by_contract(stub_mt_client: TestClient) -> None:
    resp = stub_mt_client.post(
        "/v1/render", json={"pivot_text": "Good morning", "target_languages": []}
    )
    assert resp.status_code == 422


def test_english_target_has_no_voice_so_that_result_degrades_alone(
    stub_mt_client: TestClient,
) -> None:
    # `en` is a valid LanguageCode, so the contract allows it as a target, but no English
    # Piper voice was verified in the spike. That language degrades to text-only while
    # the hi result in the same fan-out still succeeds.
    resp = stub_mt_client.post(
        "/v1/render",
        json={"pivot_text": "Good morning", "target_languages": ["en", "hi"]},
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    en, hi = parsed.results
    assert en.text == "Good morning"
    assert en.degraded.active is True
    assert en.degraded.reason == DegradedReason.TEXT_ONLY
    assert en.audio.uri == ""
    assert hi.degraded.active is False
    _assert_playable_wav(hi.audio.uri, hi.audio.duration_ms)
    assert parsed.degraded.active is True  # top-level reflects that a language degraded


def test_empty_pivot_text_is_text_only_not_an_error(stub_mt_client: TestClient) -> None:
    resp = stub_mt_client.post("/v1/render", json={"pivot_text": "   ", "target_languages": ["hi"]})
    assert resp.status_code == 200
    r = RenderResponse.model_validate(resp.json()).results[0]
    assert r.text == ""
    assert r.degraded.reason == DegradedReason.TEXT_ONLY


def test_tts_failure_degrades_only_that_language(stub_mt_client: TestClient, monkeypatch) -> None:
    real = render_app.tts_engine.synthesize

    def flaky(text, language, out_path):
        if language == LanguageCode.HINDI:
            raise RuntimeError("boom")
        return real(text, language, out_path)

    monkeypatch.setattr(render_app.tts_engine, "synthesize", flaky)
    resp = stub_mt_client.post(
        "/v1/render", json={"pivot_text": "Good morning", "target_languages": ["hi", "te"]}
    )
    hi, te = RenderResponse.model_validate(resp.json()).results
    assert hi.degraded.reason == DegradedReason.TTS_SKIPPED
    assert hi.text != "" and hi.audio.uri == ""
    assert te.degraded.active is False
    _assert_playable_wav(te.audio.uri, te.audio.duration_ms)


# ---- real MT model (skipped if the en-indic gate isn't accepted) ------------------


def test_real_mt_end_to_end_hi_and_te(real_mt_client: TestClient) -> None:
    pivot = "Good morning, may your day be peaceful."
    resp = real_mt_client.post(
        "/v1/render", json={"pivot_text": pivot, "target_languages": ["hi", "te"]}
    )
    assert resp.status_code == 200
    parsed = RenderResponse.model_validate(resp.json())
    hi, te = parsed.results
    assert any("ऀ" <= ch <= "ॿ" for ch in hi.text), hi.text  # Devanagari
    assert any("ఀ" <= ch <= "౿" for ch in te.text), te.text  # Telugu script
    for r in (hi, te):
        assert r.text != pivot
        assert r.degraded.active is False
        _assert_playable_wav(r.audio.uri, r.audio.duration_ms)
