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


# ---- WER / CER evaluation (references.csv) -------------------------------------------

TE2 = "నమస్కారం ఎలా ఉన్నారు"  # 3 words
HI_DANDA = "नमस्ते, आप कैसे हैं।"


def test_edit_distance_basics() -> None:
    assert ac.edit_distance(list("abc"), list("abc")) == 0
    assert ac.edit_distance(list("abc"), list("abd")) == 1  # substitution
    assert ac.edit_distance(list("abc"), list("ac")) == 1  # deletion
    assert ac.edit_distance(list("ac"), list("abc")) == 1  # insertion
    assert ac.edit_distance([], list("ab")) == 2 and ac.edit_distance(list("ab"), []) == 2


def test_identical_text_scores_zero() -> None:
    s = ac.score_pair(TE2, TE2, "te")
    assert (s.status, s.wer, s.cer) == ("scored", 0.0, 0.0)


def test_wer_counts_one_substitution_deletion_insertion() -> None:
    ref = "a b c d"
    assert ac.score_pair(ref, "a x c d", "en").wer == pytest.approx(0.25)
    assert ac.score_pair(ref, "a b d", "en").wer == pytest.approx(0.25)
    assert ac.score_pair(ref, "a b c d e", "en").wer == pytest.approx(0.25)


def test_wer_on_telugu_words_and_cer_on_code_points() -> None:
    hyp = "నమస్కారం ఎలా ఉన్నారా"  # last word differs by one code point (ు -> ా)
    s = ac.score_pair(TE2, hyp, "te")
    assert s.wer == pytest.approx(1 / 3)
    assert s.cer == pytest.approx(1 / len(ac.normalize(TE2, "te")))  # code points, not graphemes
    assert len(ac.normalize(TE2, "te")) == len(TE2)  # already NFC, no punctuation


def test_empty_hypothesis_against_a_reference_is_total_error() -> None:
    s = ac.score_pair(TE2, "", "te")
    assert (s.status, s.wer, s.cer) == ("scored", 1.0, 1.0)


def test_empty_reference_empty_hypothesis_is_non_speech_ok() -> None:
    s = ac.score_pair("", "", "te")
    assert (s.status, s.wer, s.cer) == ("non-speech ok", 0.0, 0.0)
    assert ac.score_pair("", "  ,। ", "hi").status == "non-speech ok"  # only punctuation


def test_empty_reference_non_empty_hypothesis_is_hallucinated() -> None:
    s = ac.score_pair("", "thank you", "en")
    assert s.status == "hallucinated" and s.hyp == "thank you" and s.wer is None


def test_normalize_nfc_and_whitespace() -> None:
    assert ac.normalize("é", "en") == "é"  # e + combining acute -> precomposed
    assert ac.normalize("  a \t b\n\nc  ", "te") == "a b c"


def test_normalize_strips_latin_danda_and_other_punctuation() -> None:
    assert ac.normalize(HI_DANDA, "hi") == "नमस्ते आप कैसे हैं"
    assert ac.normalize("నమస్కారం॥ ఎలా? ఉన్నారు!", "te") == TE2
    assert ac.normalize("“हाँ”… ठीक—है", "hi") == "हाँ ठीक है"


def test_normalize_casefolds_only_english() -> None:
    assert ac.normalize("Hello WORLD", "en") == "hello world"
    assert ac.normalize("Hello", "te") == "Hello"  # no case folding outside English
    assert ac.normalize("Hello", "hi") == "Hello"


def test_normalize_does_not_convert_digits() -> None:
    assert ac.normalize("౧౨౩ 123 १२३", "te") == "౧౨౩ 123 १२३"
    assert ac.score_pair("123", "౧౨౩", "te").wer == 1.0  # different strings, no conversion


def test_punctuation_differences_do_not_cost_errors() -> None:
    assert ac.score_pair(HI_DANDA, "नमस्ते आप कैसे हैं", "hi").wer == 0.0


# ---- references.csv reader --------------------------------------------------------------


def _csv(path: Path, text: str, bom: bool = False) -> Path:
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


CSV_TEXT = f"filename,language,reference_text\nte_short_1.wav,te,{TE2}\nhi_long_1.wav,hi,{HI}\n"


@pytest.mark.parametrize("bom", [False, True])
def test_read_refs_utf8_with_and_without_bom(tmp_path: Path, bom: bool) -> None:
    refs, problem = ac.read_refs(_csv(tmp_path / "r.csv", CSV_TEXT, bom))
    assert problem is None
    assert refs["te_short_1.wav"] == ("te", TE2) and refs["hi_long_1.wav"] == ("hi", HI)


def test_read_refs_missing_file_is_a_message_not_a_crash(tmp_path: Path) -> None:
    refs, problem = ac.read_refs(tmp_path / "nope.csv")
    assert refs == {} and problem is not None and "nope.csv" in problem


def test_read_refs_missing_columns_is_a_clear_message(tmp_path: Path) -> None:
    refs, problem = ac.read_refs(_csv(tmp_path / "r.csv", "file,text\na.wav,x\n"))
    assert refs == {} and problem is not None and "reference_text" in problem


def test_read_refs_keeps_quoted_commas(tmp_path: Path) -> None:
    text = 'filename,language,reference_text\na.wav,hi,"नमस्ते, आप"\n'
    refs, _ = ac.read_refs(_csv(tmp_path / "r.csv", text))
    assert refs["a.wav"][1] == "नमस्ते, आप"


# ---- clip classification --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "forced", "from_dir", "expected"),
    [
        ("te_short_1.wav", None, True, "te"),
        ("hi_med_2.wav", None, True, "hi"),
        ("en_long_3.wav", None, True, "en"),
        ("ns_silence.wav", None, True, None),
        ("ns_hi_noise.wav", None, True, "hi"),
        ("mystery.wav", None, True, None),
        ("mystery.wav", "te", True, "te"),  # --lang is the fallback for a directory
        ("hi_med_2.wav", "te", True, "hi"),  # ...but the prefix wins there
        ("hi_med_2.wav", "te", False, "te"),  # a single file: --lang wins, as before
    ],
)
def test_clip_language(name: str, forced: str | None, from_dir: bool, expected: str | None) -> None:
    assert ac.clip_language(name, forced, from_dir) == expected


def test_length_bucket_and_condition_come_from_the_filename() -> None:
    assert ac.length_bucket("te_short_01.wav") == "short"
    assert ac.length_bucket("te_med_noisy_01.wav") == "med"
    assert ac.length_bucket("te_long_01.wav") == "long"
    assert ac.length_bucket("te_01.wav") == "other"
    assert ac.condition("te_short_noisy_01.wav") == "noisy"
    assert ac.condition("te_short_01.wav") == "clean"
    assert ac.is_nonspeech("ns_room.wav") and not ac.is_nonspeech("te_ns.wav")


# ---- summary --------------------------------------------------------------------------


def _scored(file: str, engine: str, score: ac.Score, lang: str = "te", denoise=False) -> ac.Result:
    r = ac.Result(file, engine, "-", denoise, 1.0, [0.1], False, [score.hyp], "v", lang=lang)
    r.score = score
    return r


def _s(wer: float, cer: float) -> ac.Score:
    return ac.Score("scored", wer, cer)


def _table_rows(md: str) -> set[tuple[str, ...]]:
    return {
        tuple(c.strip() for c in line.strip("|").split("|"))
        for line in md.splitlines()
        if line.startswith("|")
    }


def test_summary_averages_per_engine_bucket_and_condition_and_excludes_no_ref() -> None:
    results = [
        _scored("te_short_1.wav", "e1", _s(0.2, 0.1)),
        _scored("te_short_noisy_2.wav", "e1", _s(0.6, 0.3)),
        _scored("te_long_3.wav", "e1", _s(1.0, 0.5)),
        _scored("te_long_4.wav", "e1", ac.Score("no ref")),
        _scored("ns_a.wav", "e1", ac.Score("non-speech ok", 0.0, 0.0)),
        _scored("ns_b.wav", "e1", ac.Score("hallucinated", None, None, "hi there")),
        _scored("te_short_1.wav", "e2", _s(0.0, 0.0)),
    ]
    md = ac.render_summary(results)
    rows = _table_rows(md)
    # per engine: lang | engine | scored | no ref | non-speech ok | hallucinated | WER | CER
    assert ("te", "e1", "3", "1", "1", "1", "60.0%", "30.0%") in rows
    assert ("te", "e2", "1", "0", "0", "0", "0.0%", "0.0%") in rows
    # per length bucket: lang | engine | bucket | scored | WER | CER
    assert ("te", "e1", "short", "2", "40.0%", "20.0%") in rows
    assert ("te", "e1", "long", "1", "100.0%", "50.0%") in rows
    # per condition
    assert ("te", "e1", "noisy", "1", "60.0%", "30.0%") in rows
    assert ("te", "e1", "clean", "2", "60.0%", "30.0%") in rows
    assert "excluded" in md.lower()


def test_summary_keeps_denoise_variants_and_languages_apart() -> None:
    results = [
        _scored("te_short_1.wav", "e", _s(0.5, 0.5)),
        _scored("te_short_1.wav", "e", _s(0.1, 0.1), denoise=True),
        _scored("hi_short_1.wav", "e", _s(0.9, 0.9), lang="hi"),
    ]
    md = ac.render_summary(results)
    assert "e +denoise" in md and "| hi | e |" in md and "| te | e |" in md


def test_summary_is_empty_without_scores() -> None:
    assert ac.render_summary([_result(TE)]) == ""


def test_markdown_wer_cer_columns_and_non_speech_cells() -> None:
    ok, bad, miss, good = _result(""), _result("Thank you"), _result(TE), _result(TE)
    ok.score = ac.Score("non-speech ok", 0.0, 0.0)
    bad.score = ac.Score("hallucinated", None, None, "Thank you")
    miss.score = ac.Score("no ref")
    good.score = ac.Score("scored", 0.125, 0.05)
    md = ac.render_markdown([ok, bad, miss, good], [], "te", runs=3)
    header = [c.strip() for c in md.splitlines()[-6].strip("|").split("|")]
    assert "WER" in header and "CER" in header
    body = md.splitlines()[-4:]
    assert "empty ok" in body[0] and "HALLUCINATED: Thank you" in body[1]
    assert "no ref | no ref" in body[2] and "12.5% | 5.0%" in body[3]


def test_markdown_without_scores_has_no_wer_columns() -> None:
    assert "WER" not in ac.render_markdown([_result(TE)], [], "te", runs=3)


# ---- CLI: --refs / --audio-dir / --lang en ---------------------------------------------


def _audio_dir(tmp_path: Path, names: list[str]) -> Path:
    d = tmp_path / "audio"
    d.mkdir()
    for n in names:
        _write_wav(d / n, np.zeros(16000), 16000)
    return d


def test_cli_lang_en_runs_whisper_only(
    tmp_path: Path, fake_models, capsys: pytest.CaptureFixture[str]
) -> None:
    wav = tmp_path / "a.wav"
    _write_wav(wav, np.zeros(16000), 16000)
    assert ac.main([str(wav), "--lang", "en"]) == 0
    out = capsys.readouterr().out
    rows = [line for line in out.splitlines() if line.startswith("| a.wav")]
    assert fake_models == ["whisper-small"] and len(rows) == 1 and "whisper-small" in rows[0]


def test_cli_audio_dir_infers_language_scores_and_summarises(
    tmp_path: Path, fake_models, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _audio_dir(tmp_path, ["te_short_1.wav", "hi_long_1.wav", "ns_room.wav", "notes.wav"])
    refs = _csv(
        tmp_path / "r.csv",
        f"filename,language,reference_text\nte_short_1.wav,te,{TE}\nhi_long_1.wav,hi,{HI}\n",
    )
    assert ac.main(["--audio-dir", str(d), "--refs", str(refs)]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    # the fake IndicConformer says TE, which equals the te reference -> 0%
    te_ctc = next(x for x in lines if x.startswith("| te_short_1.wav") and "/ctc" in x)
    assert "0.0% | 0.0%" in te_ctc
    assert "HALLUCINATED: Beep" in out  # ns_room.wav through the fake whisper
    assert "notes.wav" not in out  # no language prefix: skipped, not guessed
    assert "## Summary" in out
    assert fake_models.count("indic") == 2  # the te group and the hi group each load once


def test_cli_missing_ref_row_shows_no_ref_and_run_continues(
    tmp_path: Path, fake_models, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _audio_dir(tmp_path, ["te_short_1.wav"])
    refs = _csv(tmp_path / "r.csv", "filename,language,reference_text\nother.wav,te,x\n")
    assert ac.main(["--audio-dir", str(d), "--refs", str(refs), "--engines", "whisper-small"]) == 0
    out = capsys.readouterr().out
    assert "no ref | no ref" in out and "## Summary" in out


def test_cli_missing_refs_file_is_reported_and_rows_say_no_ref(
    tmp_path: Path, fake_models, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _audio_dir(tmp_path, ["te_short_1.wav"])
    args = [
        "--audio-dir",
        str(d),
        "--refs",
        str(tmp_path / "gone.csv"),
        "--engines",
        "whisper-small",
    ]
    assert ac.main(args) == 0
    out = capsys.readouterr().out
    assert "no ref | no ref" in out and "gone.csv" in out


def test_cli_audio_dir_en_clip_skips_indicconformer(tmp_path: Path, fake_models) -> None:
    d = _audio_dir(tmp_path, ["en_short_1.wav"])
    assert ac.main(["--audio-dir", str(d)]) == 0
    assert fake_models == ["whisper-small"]


def test_cli_needs_some_audio(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ac.main(["--lang", "te"])
    with pytest.raises(SystemExit):
        ac.main(["--audio-dir", str(tmp_path / "missing")])
