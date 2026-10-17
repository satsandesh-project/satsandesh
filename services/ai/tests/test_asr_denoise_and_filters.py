"""
Shared denoise module + faster-whisper VAD/no-speech settings. CI-safe: a fake `pyrnnoise`
and a fake WhisperModel stand in, so nothing is downloaded and pyrnnoise is not required.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
from services.ai.speech import denoise
from services.ai.speech.engine import AsrEngine, drop_no_speech_segments
from services.ai.speech.settings import Settings, SettingsError

SR = 16000


def _tone(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


# ---- resampling ---------------------------------------------------------------------


def test_resample_round_trip_preserves_length_and_a_passband_tone() -> None:
    audio = _tone()
    up = denoise.upsample_16k_to_48k(audio)
    assert len(up) == 3 * len(audio)
    back = denoise.downsample_48k_to_16k(up)
    assert len(back) == len(audio)
    mid = slice(1000, -1000)  # ignore filter edge effects
    assert np.max(np.abs(back[mid] - audio[mid])) < 0.02


# ---- mode parsing / lazy import -----------------------------------------------------


def test_parse_denoise_mode() -> None:
    assert denoise.parse_denoise_mode(None) == "off"
    assert denoise.parse_denoise_mode("") == "off"
    assert denoise.parse_denoise_mode(" RNNoise ") == "rnnoise"
    with pytest.raises(ValueError, match="ASR_DENOISE"):
        denoise.parse_denoise_mode("nope")


def test_off_never_imports_pyrnnoise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyrnnoise", None)  # an import would raise
    assert denoise.get_denoiser("off") is None


def test_rnnoise_without_the_extra_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyrnnoise", None)
    monkeypatch.setattr(denoise, "_denoisers", {})
    with pytest.raises(denoise.DenoiseUnavailableError, match=r"satsandesh-ai\[denoise\]"):
        denoise.get_denoiser("rnnoise")


# ---- RnnoiseDenoiser against a fake pyrnnoise ---------------------------------------
#
# The fake mirrors the real pyrnnoise 0.4.5 contract (read from its source), strictly
# enough that a wrong call fails: int16 [channels, samples] input, a GENERATOR of
# (speech_probs, denoised_frame[channels, 480] int16) per 10 ms frame, a zero-padded last
# frame only when partial=True, and a ragged tail silently dropped when partial=False.

_FRAME = 480  # 10 ms @ 48 kHz


class _FakeRNNoise:
    instances: ClassVar[list[_FakeRNNoise]] = []

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self.calls: list[tuple[tuple[int, ...], np.dtype, bool]] = []
        _FakeRNNoise.instances.append(self)

    def denoise_chunk(self, chunk: np.ndarray, partial: bool = False):
        chunk = np.atleast_2d(chunk)
        assert chunk.ndim == 2 and chunk.dtype == np.int16, "need int16 [channels, samples]"
        self.calls.append((chunk.shape, chunk.dtype, partial))
        n_frames = -(-chunk.shape[1] // _FRAME) if partial else chunk.shape[1] // _FRAME
        for i in range(n_frames):
            frame = chunk[:, i * _FRAME : (i + 1) * _FRAME]
            if frame.shape[1] < _FRAME:
                frame = np.pad(frame, ((0, 0), (0, _FRAME - frame.shape[1])))
            yield np.array([0.9]), frame // 2  # "denoise" = halve the level


def test_fake_is_a_generator_like_the_real_thing() -> None:
    import inspect

    assert inspect.isgeneratorfunction(_FakeRNNoise.denoise_chunk)


def test_rnnoise_denoiser_follows_the_pyrnnoise_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRNNoise.instances.clear()
    monkeypatch.setitem(sys.modules, "pyrnnoise", types.SimpleNamespace(RNNoise=_FakeRNNoise))
    audio = _tone(1.0)[:15990]  # not a multiple of the 160-sample 10 ms frame at 16 kHz
    out = denoise.RnnoiseDenoiser()(audio)

    (rnn,) = _FakeRNNoise.instances
    assert rnn.sample_rate == 48000
    assert rnn.calls == [((1, len(audio) * 3), np.dtype(np.int16), True)]  # one call, partial=True
    assert out.dtype == np.float32 and out.ndim == 1
    assert len(out) == len(audio)
    # Every yielded frame was concatenated, including the padded last one: both ends carry
    # signal (halved), not just the first frame and not a zeroed tail.
    mid, tail = slice(1000, -1000), slice(-1200, -400)
    assert 0.3 < np.abs(out[mid]).max() / np.abs(audio[mid]).max() < 0.7
    assert np.abs(out[tail]).max() > 0.05


def test_rnnoise_denoiser_uses_a_fresh_stream_per_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRNNoise.instances.clear()
    monkeypatch.setitem(sys.modules, "pyrnnoise", types.SimpleNamespace(RNNoise=_FakeRNNoise))
    d = denoise.RnnoiseDenoiser()
    d(_tone(0.5))
    d(_tone(0.5))
    assert len(_FakeRNNoise.instances) == 2


def test_rnnoise_denoiser_handles_empty_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyrnnoise", types.SimpleNamespace(RNNoise=_FakeRNNoise))
    assert denoise.RnnoiseDenoiser()(np.zeros(0, dtype=np.float32)).size == 0


# ---- faster-whisper settings --------------------------------------------------------


def test_whisper_settings_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ASR_VAD_FILTER", "ASR_NO_SPEECH_THRESHOLD", "ASR_DENOISE"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env()
    assert (s.vad_filter, s.no_speech_threshold, s.denoise_mode) == (True, 0.6, "off")

    monkeypatch.setenv("ASR_VAD_FILTER", "false")
    monkeypatch.setenv("ASR_NO_SPEECH_THRESHOLD", "0.8")
    monkeypatch.setenv("ASR_DENOISE", "rnnoise")
    s = Settings.from_env()
    assert (s.vad_filter, s.no_speech_threshold, s.denoise_mode) == (False, 0.8, "rnnoise")


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("ASR_VAD_FILTER", "yes"),
        ("ASR_NO_SPEECH_THRESHOLD", "high"),
        ("ASR_NO_SPEECH_THRESHOLD", "1.5"),
        ("ASR_DENOISE", "x"),
    ],
)
def test_whisper_settings_reject_bad_values(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(SettingsError, match=var):
        Settings.from_env()


# ---- faster-whisper engine ----------------------------------------------------------


def _seg(text: str, no_speech_prob: float, avg_logprob: float) -> SimpleNamespace:
    return SimpleNamespace(text=text, no_speech_prob=no_speech_prob, avg_logprob=avg_logprob)


def test_drop_no_speech_segments_needs_both_conditions() -> None:
    kept = [
        _seg("confident despite high no_speech", 0.9, -0.3),
        _seg("low no_speech, poor logprob", 0.2, -1.5),
        _seg("at the threshold", 0.6, -1.5),  # not strictly greater
    ]
    dropped = [_seg("hallucinated", 0.7, -1.2)]
    assert drop_no_speech_segments(kept + dropped, 0.6) == kept


class _FakeWhisper:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        segs = [_seg(" real", 0.1, -0.2), _seg(" ghost", 0.95, -2.0)]
        return iter(segs), SimpleNamespace(language="en")


def test_engine_passes_vad_and_no_condition_and_filters_segments() -> None:
    engine = AsrEngine("small", "int8", 1, vad_filter=True, no_speech_threshold=0.6)
    engine._model = _FakeWhisper()
    result = engine.transcribe(_tone(), "en")
    assert engine._model.kwargs == {
        "language": "en",
        "vad_filter": True,
        "condition_on_previous_text": False,
    }
    assert result.text == "real"


def test_engine_honours_vad_off_and_a_custom_threshold() -> None:
    engine = AsrEngine("small", "int8", 1, vad_filter=False, no_speech_threshold=0.99)
    engine._model = _FakeWhisper()
    result = engine.transcribe(_tone(), "en")
    assert engine._model.kwargs["vad_filter"] is False
    assert result.text == "real ghost"


# ---- tools/denoise_ab.py ------------------------------------------------------------


def test_denoise_ab_side_by_side_and_wav_roundtrip(tmp_path) -> None:
    from services.ai.tools import denoise_ab

    table = denoise_ab.side_by_side("alpha beta", "", width=12)
    assert "denoise OFF" in table and "denoise ON" in table
    assert "alpha beta" in table and "(empty)" in table

    path = tmp_path / "a.wav"
    denoise_ab.write_wav(path, _tone(0.5))
    assert np.max(np.abs(denoise_ab.load_audio(path) - _tone(0.5))) < 1e-3
