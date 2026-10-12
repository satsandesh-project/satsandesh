"""
CI-safe tests for services/ai/tools/asr_compare.py: script detection and shares, audio
loading, run bookkeeping, percentile gating, Markdown output and the CLI wiring. Mocked
engines and denoiser only -- no models, no network, no audio recordings.
"""

import wave
from pathlib import Path

import numpy as np
import pytest
from services.ai.tools import asr_compare as ac

TE = "నమస్కారం ఎలా ఉన్నారు"
HI = "नमस्ते आप कैसे हैं"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (TE, "Telugu"),
        (HI, "Devanagari"),
        ("Beep", "Latin"),
        ("నమస్కారం hello", "mixed"),
        ("", "none"),
        ("  123 ,.! ", "none"),
    ],
)
def test_detect_script(text: str, expected: str) -> None:
    assert ac.detect_script(text) == expected


def test_script_shares_are_shares_of_letters() -> None:
    assert ac.script_shares(TE) == {"Telugu": 100.0, "Devanagari": 0.0, "Latin": 0.0}
    assert ac.script_shares(HI)["Devanagari"] == 100.0
    mixed = ac.script_shares("ab" + "క" * 2)  # 2 Latin + 2 Telugu letters
    assert mixed["Telugu"] == 50.0 and mixed["Latin"] == 50.0 and mixed["Devanagari"] == 0.0


def test_script_shares_empty_is_none_not_zero() -> None:
    assert ac.script_shares("") == {"Telugu": None, "Devanagari": None, "Latin": None}
    assert ac.script_shares("123 !!") == {"Telugu": None, "Devanagari": None, "Latin": None}


def test_script_shares_other_scripts_count_in_the_denominator() -> None:
    shares = ac.script_shares("కక" + "ب" * 2)  # Telugu + Arabic letters
    assert shares["Telugu"] == 50.0


# ---- audio ------------------------------------------------------------------------


def _write_wav(path: Path, samples: np.ndarray, rate: int, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())


def test_load_audio_wav_passthrough_and_resample(tmp_path: Path) -> None:
    _write_wav(tmp_path / "a.wav", np.zeros(1600), 16000)
    audio, note = ac.load_audio(tmp_path / "a.wav")
    assert audio.dtype == np.float32 and audio.size == 1600 and note is None

    t = np.arange(48000) / 48000
    _write_wav(tmp_path / "b.wav", 0.5 * np.sin(2 * np.pi * 440 * t), 48000)
    audio, note = ac.load_audio(tmp_path / "b.wav")
    assert audio.size == 16000 and note is not None and "48000" in note


def test_load_audio_rejects_stereo_rather_than_mixing(tmp_path: Path) -> None:
    _write_wav(tmp_path / "s.wav", np.zeros(3200), 16000, channels=2)
    with pytest.raises(ValueError, match="mono"):
        ac.load_audio(tmp_path / "s.wav")


def test_load_audio_non_wav_goes_through_the_service_ffmpeg_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.ai.speech.engine as speech_engine

    seen: list[Path] = []

    def fake_decode(path: Path) -> np.ndarray:
        seen.append(path)
        return np.zeros(800, dtype=np.float32)

    monkeypatch.setattr(speech_engine, "decode_via_ffmpeg", fake_decode)
    audio, note = ac.load_audio(tmp_path / "clip.opus")
    assert seen == [tmp_path / "clip.opus"] and audio.size == 800 and ".opus" in (note or "")


# ---- run bookkeeping -----------------------------------------------------------------


def _eng(text="x", fresh=True, name="e", mode="-") -> ac.LoadedEngine:
    return ac.LoadedEngine(name, mode, "v1", lambda a: text, 0.1, fresh=fresh)


def _run(eng, clips, denoise_fn=None, runs=3, lang="te") -> list[ac.Result]:
    out: list[ac.Result] = []
    ac.run_engine(eng, clips, denoise_fn, runs, lang, out.append)
    return out


CLIPS = [("a.wav", np.zeros(16000, np.float32)), ("b.wav", np.zeros(32000, np.float32))]


def test_run_engine_runs_each_clip_and_only_the_first_is_cold() -> None:
    results = _run(_eng(TE), CLIPS, runs=3)
    assert [(r.file, len(r.run_times_s)) for r in results] == [("a.wav", 3), ("b.wav", 3)]
    assert [r.first_is_cold for r in results] == [True, False]
    assert results[1].audio_s == 2.0


def test_denoise_adds_a_row_per_clip_and_its_output_reaches_the_engine() -> None:
    seen: list[np.ndarray] = []
    eng = ac.LoadedEngine("e", "-", "v", lambda a: seen.append(a) or "x", 0.0)
    marker = np.full(16000, 0.5, dtype=np.float32)
    results = _run(eng, CLIPS[:1], denoise_fn=lambda a: marker, runs=2)
    assert [r.denoise for r in results] == [False, True]
    assert seen[0] is not marker and seen[2] is marker  # off run first, then 2 denoised runs


def test_a_second_mode_on_an_already_warm_model_is_not_cold() -> None:
    assert _run(_eng(fresh=False), CLIPS[:1], runs=1)[0].first_is_cold is False


def test_consistency_flag_notices_differing_runs() -> None:
    texts = iter(["a", "b", "a"])
    eng = ac.LoadedEngine("e", "-", "v", lambda a: next(texts), 0.0)
    (result,) = _run(eng, CLIPS[:1], runs=3)
    assert result.consistent is False and result.transcript == "a"


@pytest.mark.parametrize(
    ("runs", "expect"), [(1, False), (3, False), (4, False), (5, True), (9, True)]
)
def test_percentiles_only_from_five_runs(runs: int, expect: bool) -> None:
    r = ac.Result(
        "a", "e", "-", False, 1.0, [float(i + 1) for i in range(runs)], False, ["x"] * runs, "v"
    )
    assert (r.percentiles() is not None) is expect
    if expect:
        p50, p90 = r.percentiles()
        assert p50 == pytest.approx(np.percentile(r.run_times_s, 50))
        assert p90 == pytest.approx(np.percentile(r.run_times_s, 90))


# ---- markdown ------------------------------------------------------------------------


def _result(text: str, runs: int = 3, **kw) -> ac.Result:
    return ac.Result(
        "a.wav", "indicconformer", "ctc", kw.get("denoise", False), 4.0,
        [1.0] * runs, True, [text] * runs, "m@1",
    )  # fmt: skip


def test_markdown_has_script_columns_empty_verdict_and_no_percentiles_below_five() -> None:
    md = ac.render_markdown([_result("నమస్కారం | లైన్\nబ్రేక్")], ["CPU: test"], "te", runs=3)
    table = [line for line in md.splitlines() if line.startswith("|")]
    assert len(table) == 3
    header = [c.strip() for c in table[0].strip("|").split("|")]
    assert "Telugu %" in header and "Devanagari %" in header and "Latin %" in header
    assert "p50 (s)" not in header and "p90 (s)" not in header
    assert header[-1] == "my verdict" and table[2].rstrip().endswith("|  |")
    assert "100% | 0% | 0%" in table[2]
    assert "నమస్కారం \\| లైన్ బ్రేక్" in table[2]
    assert "(cold)" in table[2] and "- CPU: test" in md and "No ground truth" in md


def test_markdown_adds_percentile_columns_at_five_runs() -> None:
    md = ac.render_markdown([_result(TE, runs=5)], [], "te", runs=5)
    assert "p50 (s)" in md and "p90 (s)" in md


def test_markdown_expected_script_follows_the_language() -> None:
    md = ac.render_markdown([_result(HI)], [], "hi", runs=3)
    assert "Devanagari %" in md and "Telugu %" not in md.splitlines()[-3]
    assert "100% | 100% | 0%" in md  # expected (Devanagari) | Devanagari | Latin


def test_markdown_empty_transcript_is_marked_and_shares_are_dashes() -> None:
    md = ac.render_markdown([_result("")], [], "te", runs=3)
    assert "(empty)" in md and "- | - | -" in md


# ---- CLI ----------------------------------------------------------------------------


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch):
    loaded: list[str] = []

    def fake_indic(modes, lang):
        loaded.append("indic")
        return [
            ac.LoadedEngine("indicconformer", m, f"ic-{m}", lambda a: TE, 0.0, fresh=(i == 0))
            for i, m in enumerate(modes)
        ]

    def fake_whisper(size, lang, threads):
        loaded.append(f"whisper-{size}")
        return ac.LoadedEngine(f"whisper-{size}", "-", "fw", lambda a: "Beep", 0.0)

    monkeypatch.setattr(ac, "_load_indic", fake_indic)
    monkeypatch.setattr(ac, "_load_whisper", fake_whisper)
    return loaded


def test_cli_runs_all_three_engines_prints_a_table_and_writes_out(
    tmp_path: Path, fake_models, capsys: pytest.CaptureFixture[str]
) -> None:
    wav = tmp_path / "a.wav"
    _write_wav(wav, np.zeros(16000), 16000)
    out = tmp_path / "cmp.md"
    assert ac.main([str(wav), "--lang", "te", "--out", str(out)]) == 0

    stdout = capsys.readouterr().out
    assert out.read_text(encoding="utf-8").strip() == stdout.strip()
    rows = [line for line in stdout.splitlines() if line.startswith("| a.wav")]
    assert [("indicconformer/ctc" in r, "indicconformer/rnnt" in r, "whisper-small" in r) for r in rows] == [
        (True, False, False), (False, True, False), (False, False, True),
    ]  # fmt: skip
    assert fake_models == ["indic", "whisper-small"]  # one model load each, no medium


def test_cli_denoise_flag_adds_denoised_rows(
    tmp_path: Path, fake_models, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ac, "get_denoise_fn", lambda: lambda a: a)
    wav = tmp_path / "a.wav"
    _write_wav(wav, np.zeros(16000), 16000)
    ac.main([str(wav), "--lang", "te", "--engines", "whisper-small", "--denoise"])
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("| a.wav")]
    assert len(rows) == 2 and " off " in rows[0] and " on " in rows[1]


def test_cli_without_denoise_never_touches_the_denoiser(
    tmp_path: Path, fake_models, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom():
        raise AssertionError("denoiser must not be loaded without --denoise")

    monkeypatch.setattr(ac, "get_denoise_fn", boom)
    wav = tmp_path / "a.wav"
    _write_wav(wav, np.zeros(16000), 16000)
    assert ac.main([str(wav), "--lang", "hi", "--engines", "whisper-small"]) == 0


def test_cli_runs_default_is_three_and_percentiles_appear_at_five(
    tmp_path: Path, fake_models, capsys: pytest.CaptureFixture[str]
) -> None:
    wav = tmp_path / "a.wav"
    _write_wav(wav, np.zeros(16000), 16000)
    ac.main([str(wav), "--lang", "te", "--engines", "whisper-small"])
    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if line.startswith("| a.wav"))
    assert row.count(" / ") == 2 and "p50 (s)" not in out and "needs --runs >= 5" in out

    ac.main([str(wav), "--lang", "te", "--engines", "whisper-small", "--runs", "5"])
    assert "p50 (s)" in capsys.readouterr().out


def test_cli_unreadable_input_is_skipped_and_none_usable_exits_1(
    tmp_path: Path, fake_models
) -> None:
    _write_wav(tmp_path / "stereo.wav", np.zeros(3200), 16000, channels=2)
    assert ac.main([str(tmp_path / "stereo.wav"), "--lang", "te"]) == 1
    assert fake_models == []


def test_cli_an_engine_that_cannot_load_is_reported_and_the_rest_still_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(modes, lang):
        raise RuntimeError("no weights")

    monkeypatch.setattr(ac, "_load_indic", broken)
    monkeypatch.setattr(
        ac,
        "_load_whisper",
        lambda s, lang, t: ac.LoadedEngine("whisper-small", "-", "fw", lambda a: "x", 0.0),
    )
    wav = tmp_path / "a.wav"
    _write_wav(wav, np.zeros(16000), 16000)
    assert ac.main([str(wav), "--lang", "te"]) == 0
    out = capsys.readouterr().out
    assert "SKIPPED, could not load (RuntimeError: no weights)" in out and "whisper-small" in out


def test_hf_token_comes_from_env_and_offline_needs_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        ac._hf_token()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert ac._hf_token()  # a placeholder, not a secret
    monkeypatch.setenv("HF_TOKEN", "hf_from_env")
    assert ac._hf_token() == "hf_from_env"


def test_cli_requires_lang_and_rejects_unknown_engine(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ac.main([str(tmp_path / "a.wav")])
    with pytest.raises(SystemExit):
        ac.main([str(tmp_path / "a.wav"), "--lang", "te", "--engines", "whisper-medium"])
