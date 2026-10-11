"""
Language auto-detect restricted to en/hi/te (#134). CI-safe: a stubbed WhisperModel stands
in, so nothing is downloaded. The stub's `detect_language` mirrors faster-whisper 1.2.1's
real return shape: (best_language, best_prob, [(code, prob), ...]) with the codes already
stripped of their `<|..|>` markers and the list sorted by descending probability.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pytest
from services.ai.speech.engine import AsrEngine, UnsupportedLanguageError
from services.ai.speech.settings import Settings, SettingsError


class _StubWhisper:
    def __init__(self, probs: dict[str, float]) -> None:
        ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        self._detect_result = (ranked[0][0], ranked[0][1], ranked)
        self.detect_calls = 0
        self.transcribe_languages: list[str | None] = []

    def detect_language(self, audio=None, **kwargs):
        self.detect_calls += 1
        return self._detect_result

    def transcribe(self, audio, **kwargs):
        language = kwargs["language"]
        self.transcribe_languages.append(language)
        # Like faster-whisper: with no language it would detect on its own (possibly an
        # unsupported one); with one it echoes it. The engine must always pass one.
        seg = SimpleNamespace(text=" hello", no_speech_prob=0.0, avg_logprob=-0.1)
        return iter([seg]), SimpleNamespace(language=language or "nn")


def _engine(probs: dict[str, float], **kwargs) -> tuple[AsrEngine, _StubWhisper]:
    engine = AsrEngine("small", "int8", 1, **kwargs)
    stub = _StubWhisper(probs)
    engine._model = stub
    return engine, stub


AUDIO = np.zeros(16000, dtype=np.float32)

# 'nn' (Norwegian Nynorsk) wins overall, but te is the best of the three supported.
UNSUPPORTED_TOP = {"nn": 0.50, "si": 0.20, "ur": 0.10, "te": 0.08, "hi": 0.07, "en": 0.05}


def test_no_hint_picks_best_of_the_three_even_when_unsupported_scores_highest() -> None:
    engine, stub = _engine(UNSUPPORTED_TOP)
    result = engine.transcribe(AUDIO, None)
    assert result.detected_language == "te"
    assert stub.transcribe_languages == ["te"]


def test_no_hint_never_returns_a_language_outside_the_three() -> None:
    engine, _ = _engine({"ur": 0.9, "en": 0.04, "hi": 0.03, "te": 0.03})
    assert engine.transcribe(AUDIO, None).detected_language == "en"


def test_forced_hint_skips_detection_and_reports_the_hint() -> None:
    engine, stub = _engine({"en": 0.95, "hi": 0.03, "te": 0.02})
    result = engine.transcribe(AUDIO, "hi")
    assert result.detected_language == "hi"
    assert stub.transcribe_languages == ["hi"]
    assert stub.detect_calls == 0


@pytest.mark.parametrize("hint", ["fr", "nn", "ur", ""])
def test_hint_outside_the_three_is_the_callers_error(hint: str) -> None:
    engine, stub = _engine(UNSUPPORTED_TOP)
    with pytest.raises(UnsupportedLanguageError):
        engine.transcribe(AUDIO, hint)
    assert stub.transcribe_languages == []


def test_detected_probabilities_are_logged_at_info(caplog) -> None:
    engine, _ = _engine(UNSUPPORTED_TOP)
    with caplog.at_level(logging.INFO, logger="services.ai.speech.engine"):
        engine.transcribe(AUDIO, None)
    text = caplog.text
    assert "nn=0.500" in text and "te=0.080" in text
    assert "chosen=te" in text


# ---- ASR_HINT_MODE=tiebreak --------------------------------------------------------


def test_tiebreak_keeps_the_hint_when_the_margin_is_not_exceeded() -> None:
    engine, stub = _engine({"en": 0.60, "hi": 0.30, "te": 0.10}, hint_mode="tiebreak")
    result = engine.transcribe(AUDIO, "hi")  # en leads by 0.30 <= 0.5
    assert result.detected_language == "hi"
    assert stub.transcribe_languages == ["hi"]


def test_tiebreak_overrides_the_hint_when_another_language_is_clearly_more_probable() -> None:
    engine, stub = _engine({"en": 0.90, "hi": 0.05, "te": 0.05}, hint_mode="tiebreak")
    result = engine.transcribe(AUDIO, "hi")  # en leads by 0.85 > 0.5
    assert result.detected_language == "en"  # truthful: what was actually decoded
    assert stub.transcribe_languages == ["en"]


def test_tiebreak_margin_is_configurable() -> None:
    engine, _ = _engine(
        {"en": 0.60, "hi": 0.30, "te": 0.10}, hint_mode="tiebreak", hint_override_margin=0.2
    )
    assert engine.transcribe(AUDIO, "hi").detected_language == "en"


def test_tiebreak_ignores_unsupported_languages_when_comparing() -> None:
    # 'nn' is huge but irrelevant: restricted to the three, hi (0.3) vs en (0.2) is no override.
    engine, _ = _engine({"nn": 0.9, "hi": 0.3, "en": 0.2, "te": 0.1}, hint_mode="tiebreak")
    assert engine.transcribe(AUDIO, "hi").detected_language == "hi"


def test_tiebreak_with_no_hint_just_detects() -> None:
    engine, _ = _engine(UNSUPPORTED_TOP, hint_mode="tiebreak")
    assert engine.transcribe(AUDIO, None).detected_language == "te"


def test_tiebreak_unsupported_hint_still_errors() -> None:
    engine, _ = _engine(UNSUPPORTED_TOP, hint_mode="tiebreak")
    with pytest.raises(UnsupportedLanguageError):
        engine.transcribe(AUDIO, "fr")


def test_detection_without_speech_falls_back_to_unfiltered_detection() -> None:
    # faster-whisper's vad_filter detection raises on audio with no speech chunks.
    engine, stub = _engine(UNSUPPORTED_TOP, vad_filter=True)
    real = stub.detect_language
    calls: list[bool] = []

    def flaky(audio=None, **kwargs):
        calls.append(kwargs.get("vad_filter", False))
        if kwargs.get("vad_filter"):
            raise ValueError("need at least one array to concatenate")
        return real(audio, **kwargs)

    stub.detect_language = flaky
    assert engine.transcribe(AUDIO, None).detected_language == "te"
    assert calls == [True, False]


# ---- settings ----------------------------------------------------------------------


def _env(monkeypatch, **env: str) -> None:
    for key in ("ASR_HINT_MODE", "ASR_HINT_OVERRIDE_MARGIN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_settings_defaults_are_force_and_half(monkeypatch) -> None:
    _env(monkeypatch)
    s = Settings.from_env()
    assert (s.hint_mode, s.hint_override_margin) == ("force", 0.5)


def test_settings_accepts_tiebreak_and_margin(monkeypatch) -> None:
    _env(monkeypatch, ASR_HINT_MODE=" TieBreak ", ASR_HINT_OVERRIDE_MARGIN="0.3")
    s = Settings.from_env()
    assert (s.hint_mode, s.hint_override_margin) == ("tiebreak", 0.3)


@pytest.mark.parametrize(
    "env",
    [
        {"ASR_HINT_MODE": "auto"},
        {"ASR_HINT_MODE": ""},
        {"ASR_HINT_OVERRIDE_MARGIN": "abc"},
        {"ASR_HINT_OVERRIDE_MARGIN": "-0.1"},
        {"ASR_HINT_OVERRIDE_MARGIN": "1.5"},
        {"ASR_HINT_OVERRIDE_MARGIN": "nan"},
    ],
)
def test_settings_rejects_invalid_values_at_startup(monkeypatch, env) -> None:
    _env(monkeypatch, **env)
    with pytest.raises(SettingsError):
        Settings.from_env()
