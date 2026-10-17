"""
No-speech guard and denoise wiring for services/ai/speech_indicconformer/. Stub engine and
fake denoiser: no model, no HF_TOKEN, no pyrnnoise.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
import services.ai.speech_indicconformer.app as ic_app
from contracts.ai.errors import ErrorCode, PipelineError
from contracts.ai.transcribe import TranscribeResponse
from fastapi.testclient import TestClient
from services.ai.speech_indicconformer.engine import TranscriptionResult
from services.ai.speech_indicconformer.no_speech import is_no_speech, spectral_flatness
from services.ai.speech_indicconformer.settings import Settings, SettingsError

SR = 16000
_RNG = np.random.default_rng(0)


def _tone(seconds: float = 2.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _silence(seconds: float = 3.0) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def _white_noise(seconds: float = 3.0) -> np.ndarray:
    return (0.1 * _RNG.standard_normal(int(SR * seconds))).clip(-1, 1).astype(np.float32)


def _speech_like(seconds: float = 3.0) -> np.ndarray:
    """Harmonic stack with a syllable-rate envelope: coloured, not flat, clearly loud."""
    t = np.arange(int(SR * seconds)) / SR
    carrier = sum(np.sin(2 * np.pi * 140 * k * t) / k for k in range(1, 12))
    envelope = 0.5 * (1 + np.sin(2 * np.pi * 4 * t))
    return (0.15 * carrier * envelope).astype(np.float32)


def _write_wav(path: Path, audio: np.ndarray) -> str:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return "file:///" + str(path).replace("\\", "/")


# ---- guard --------------------------------------------------------------------------


def test_guard_flags_silence_and_white_noise() -> None:
    assert is_no_speech(_silence())
    assert is_no_speech(_white_noise())
    assert is_no_speech(np.zeros(0, dtype=np.float32))


def test_guard_lets_tone_and_speech_like_audio_through() -> None:
    assert not is_no_speech(_tone())
    assert not is_no_speech(_speech_like())


def test_spectral_flatness_orders_noise_above_tone() -> None:
    assert spectral_flatness(_white_noise()) > 0.5 > spectral_flatness(_tone())


# ---- app: guard ---------------------------------------------------------------------


class CountingEngine:
    model_version = "stub-indicconformer-ctc"
    decode_mode = "ctc"
    load_duration_ms = 1.0
    warmup_duration_ms = 1.0

    def __init__(self) -> None:
        self.seen: list[np.ndarray] = []

    def transcribe(self, audio, language, decode_mode=None) -> TranscriptionResult:
        from services.ai.speech_indicconformer.engine import UnsupportedLanguageError

        if language not in ("hi", "te"):
            raise UnsupportedLanguageError(f"language {language!r} is not supported")
        self.seen.append(audio)
        return TranscriptionResult("నమస్కారం", language, "ctc", 5.0, 0.1)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> CountingEngine:
    eng = CountingEngine()
    monkeypatch.setattr(ic_app, "engine", eng)
    monkeypatch.setattr(ic_app, "_ready", True)
    monkeypatch.setattr(ic_app, "denoiser", None)
    monkeypatch.setattr(ic_app, "no_speech_guard", True)
    return eng


def _post(uri: str, hint: str | None = "te"):
    payload: dict = {"audio": {"uri": uri, "format": "wav_pcm16"}}
    if hint:
        payload["language_hint"] = hint
    return TestClient(ic_app.app).post("/v1/transcribe", json=payload)


@pytest.mark.parametrize("make", [_silence, _white_noise])
@pytest.mark.parametrize("hint", ["te", "hi"])
def test_non_speech_returns_empty_text_without_running_the_model(
    engine: CountingEngine, tmp_path: Path, make, hint: str
) -> None:
    resp = _post(_write_wav(tmp_path / "x.wav", make()), hint)
    assert resp.status_code == 200
    parsed = TranscribeResponse.model_validate(resp.json())
    assert parsed.text == ""
    assert parsed.detected_language.value == hint
    assert engine.seen == []


def test_speech_like_audio_still_reaches_the_model(engine: CountingEngine, tmp_path: Path) -> None:
    resp = _post(_write_wav(tmp_path / "x.wav", _speech_like()))
    assert resp.status_code == 200
    assert resp.json()["text"] == "నమస్కారం"
    assert len(engine.seen) == 1


def test_unsupported_language_still_rejected_on_silence(
    engine: CountingEngine, tmp_path: Path
) -> None:
    resp = _post(_write_wav(tmp_path / "x.wav", _silence()), hint=None)
    assert resp.status_code == 422
    assert PipelineError.model_validate(resp.json()).code == ErrorCode.UNSUPPORTED_LANGUAGE


def test_guard_can_be_switched_off(
    engine: CountingEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ic_app, "no_speech_guard", False)
    resp = _post(_write_wav(tmp_path / "x.wav", _silence()))
    assert resp.json()["text"] == "నమస్కారం"
    assert len(engine.seen) == 1


# ---- app: denoise -------------------------------------------------------------------


def test_denoise_runs_before_guard_and_asr(
    engine: CountingEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_denoiser(audio: np.ndarray) -> np.ndarray:
        calls.append(len(audio))
        return _speech_like(len(audio) / SR)  # "cleaned" audio is what the guard/ASR see

    monkeypatch.setattr(ic_app, "denoiser", fake_denoiser)
    # Input is noise (guard alone would block it); denoise runs first, so it gets through.
    resp = _post(_write_wav(tmp_path / "x.wav", _white_noise()))
    body = resp.json()
    assert resp.status_code == 200
    assert body["text"] == "నమస్కారం"
    assert calls == [SR * 3]
    assert [s["stage"] for s in body["stage_timings"]] == [
        "decode",
        "denoise",
        "inference",
        "postprocess",
    ]


def test_no_denoise_stage_when_off(engine: CountingEngine, tmp_path: Path) -> None:
    body = _post(_write_wav(tmp_path / "x.wav", _speech_like())).json()
    assert [s["stage"] for s in body["stage_timings"]] == ["decode", "inference", "postprocess"]


def test_denoise_failure_falls_back_to_original_audio(
    engine: CountingEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_audio: np.ndarray) -> np.ndarray:
        raise RuntimeError("rnnoise exploded")

    monkeypatch.setattr(ic_app, "denoiser", boom)
    resp = _post(_write_wav(tmp_path / "x.wav", _speech_like()))
    assert resp.status_code == 200
    assert resp.json()["text"] == "నమస్కారం"


# ---- settings -----------------------------------------------------------------------


def test_settings_denoise_and_guard_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    for var in ("ASR_DENOISE", "ASR_NO_SPEECH_GUARD"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert s.denoise_mode == "off"
    assert s.no_speech_guard is True


def test_settings_denoise_and_guard_parse_and_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    monkeypatch.setenv("ASR_DENOISE", "RNNoise")
    monkeypatch.setenv("ASR_NO_SPEECH_GUARD", "off")
    s = Settings.from_env()
    assert (s.denoise_mode, s.no_speech_guard) == ("rnnoise", False)
    monkeypatch.setenv("ASR_DENOISE", "deepfilter")
    with pytest.raises(SettingsError, match="ASR_DENOISE"):
        Settings.from_env()
    monkeypatch.setenv("ASR_DENOISE", "off")
    monkeypatch.setenv("ASR_NO_SPEECH_GUARD", "maybe")
    with pytest.raises(SettingsError, match="ASR_NO_SPEECH_GUARD"):
        Settings.from_env()


def test_gate_thresholds_are_configurable_and_gated_clips_are_logged(
    engine: CountingEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    uri = _write_wav(tmp_path / "quiet.wav", _speech_like() * 0.01)  # rms ~ 0.0005
    # Default min_rms (0.003) gates it, and the gated clip is logged with its measurements.
    with caplog.at_level("WARNING", logger="services.ai.speech_indicconformer.app"):
        assert _post(uri).json()["text"] == ""
    assert any("no-speech gate" in r.message and "quiet.wav" in r.message for r in caplog.records)
    assert engine.seen == []

    # Lowering the threshold lets the same clip reach the model.
    monkeypatch.setattr(ic_app, "no_speech_min_rms", 0.0001)
    assert _post(uri).json()["text"] == "నమస్కారం"
    assert len(engine.seen) == 1


def test_settings_gate_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_dummy")
    for var in ("ASR_NO_SPEECH_MIN_RMS", "ASR_NO_SPEECH_MAX_FLATNESS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert (s.no_speech_min_rms, s.no_speech_max_flatness) == (0.003, 0.5)

    monkeypatch.setenv("ASR_NO_SPEECH_MIN_RMS", "0.01")
    monkeypatch.setenv("ASR_NO_SPEECH_MAX_FLATNESS", "0.7")
    s = Settings.from_env()
    assert (s.no_speech_min_rms, s.no_speech_max_flatness) == (0.01, 0.7)

    monkeypatch.setenv("ASR_NO_SPEECH_MAX_FLATNESS", "2")
    with pytest.raises(SettingsError, match="ASR_NO_SPEECH_MAX_FLATNESS"):
        Settings.from_env()
    monkeypatch.setenv("ASR_NO_SPEECH_MAX_FLATNESS", "0.5")
    monkeypatch.setenv("ASR_NO_SPEECH_MIN_RMS", "loud")
    with pytest.raises(SettingsError, match="ASR_NO_SPEECH_MIN_RMS"):
        Settings.from_env()
