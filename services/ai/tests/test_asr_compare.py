"""
CI-safe tests for services/ai/tools/asr_compare.py: script detection, WAV reading /
resampling, the row builder, run labelling and the CSV/Markdown writers. Fake engines
only -- no models, no network.
"""

import csv
import wave
from pathlib import Path

import numpy as np
import pytest
from services.ai.tools import asr_compare as ac


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("నమస్కారం ఎలా ఉన్నారు", "Telugu"),
        ("नमस्ते आप कैसे हैं", "Devanagari"),
        ("Beep", "Latin"),
        ("నమస్కారం hello", "mixed"),
        ("నమస్కారం नमस्ते", "mixed"),
        ("", "none"),
        ("  123 ,.! ", "none"),
        ("నమస్కారం 123, ?", "Telugu"),
    ],
)
def test_detect_script(text: str, expected: str) -> None:
    assert ac.detect_script(text) == expected


def _write_wav(path: Path, samples: np.ndarray, rate: int, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((samples * 32767).astype(np.int16).tobytes())


def test_read_wav_16k_passes_through_without_note(tmp_path: Path) -> None:
    _write_wav(tmp_path / "a.wav", np.zeros(1600), 16000)
    audio, note = ac.read_wav_16k_mono(tmp_path / "a.wav")
    assert audio.dtype == np.float32
    assert audio.size == 1600
    assert note is None


def test_read_wav_resamples_and_says_so(tmp_path: Path) -> None:
    t = np.arange(48000) / 48000
    _write_wav(tmp_path / "a.wav", 0.5 * np.sin(2 * np.pi * 440 * t), 48000)
    audio, note = ac.read_wav_16k_mono(tmp_path / "a.wav")
    assert audio.size == 16000
    assert note is not None
    assert "48000" in note
    assert 0.4 < float(np.abs(audio).max()) < 0.6  # amplitude preserved


def test_read_wav_rejects_stereo_rather_than_mixing(tmp_path: Path) -> None:
    _write_wav(tmp_path / "s.wav", np.zeros(3200), 16000, channels=2)
    with pytest.raises(ValueError, match="mono"):
        ac.read_wav_16k_mono(tmp_path / "s.wav")


def test_make_row_computes_rtf_and_script() -> None:
    row = ac.make_row(
        file="a.wav",
        engine="whisper-small",
        mode="-",
        denoise=False,
        run=1,
        cold=True,
        wall_s=3.0,
        audio_s=2.0,
        transcript="Beep",
        model_version="m@1",
    )
    assert row.rtf == 1.5
    assert row.script == "Latin"
    assert row.state == "cold"
    assert row.denoise == "off"
    assert row.my_verdict == ""


def test_run_engine_labels_only_the_first_inference_cold_and_covers_denoise() -> None:
    eng = ac.LoadedEngine("e", "-", "v1", lambda a: "నమస్కారం", 0.1)
    clips = [("a.wav", np.zeros(16000, np.float32)), ("b.wav", np.zeros(32000, np.float32))]
    rows: list[ac.Row] = []
    ac.run_engine(eng, clips, lambda a: a, runs=2, on_row=rows.append)
    assert len(rows) == 2 * 2 * 2  # clips x (off,on) x runs
    assert [r.state for r in rows].count("cold") == 1
    assert rows[0].state == "cold"
    assert {r.denoise for r in rows} == {"off", "on"}
    assert all(r.script == "Telugu" for r in rows)
    assert rows[-1].audio_s == 2.0


def test_run_engine_without_denoise_only_has_off_rows() -> None:
    eng = ac.LoadedEngine("e", "-", "v1", lambda a: "x", 0.1)
    rows: list[ac.Row] = []
    ac.run_engine(eng, [("a.wav", np.zeros(16000, np.float32))], None, 2, rows.append)
    assert {r.denoise for r in rows} == {"off"}


def test_shared_model_second_mode_is_not_labelled_cold() -> None:
    eng = ac.LoadedEngine("e", "rnnt", "v", lambda a: "x", 0.0, fresh=False)
    rows: list[ac.Row] = []
    ac.run_engine(eng, [("a.wav", np.zeros(16000, np.float32))], None, 1, rows.append)
    assert rows[0].state == "warm"


def _rows() -> list[ac.Row]:
    return [
        ac.make_row(
            file="a.wav",
            engine="indicconformer",
            mode="ctc",
            denoise=True,
            run=2,
            cold=False,
            wall_s=1.0,
            audio_s=4.0,
            transcript="నమస్కారం | లైన్\nబ్రేక్",
            model_version="ai4bharat@onnx",
        )
    ]


def test_csv_round_trips_telugu_and_has_empty_verdict(tmp_path: Path) -> None:
    out = tmp_path / "r.csv"
    ac.write_csv(_rows(), out)
    with out.open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    assert list(records[0]) == [
        "file", "engine", "mode", "denoise", "run", "state", "wall_s", "audio_s",
        "rtf", "transcript", "script", "model_version", "my_verdict",
    ]  # fmt: skip
    assert records[0]["transcript"] == "నమస్కారం | లైన్\nబ్రేక్"
    assert records[0]["script"] == "Telugu"
    assert records[0]["my_verdict"] == ""
    assert records[0]["rtf"] == "0.25"


def test_markdown_table_is_one_line_per_row_and_escapes_pipes() -> None:
    md = ac.render_markdown(_rows(), ["CPU: test"])
    assert "- CPU: test" in md
    table = [line for line in md.splitlines() if line.startswith("|")]
    assert len(table) == 3  # header, separator, one row
    assert "నమస్కారం \\| లైన్ బ్రేక్" in table[2]
    assert "No ground truth" in md


def test_load_denoise_skips_cleanly_when_unavailable() -> None:
    fn, message = ac.load_denoise()
    if fn is None:
        assert "skipped" in message
