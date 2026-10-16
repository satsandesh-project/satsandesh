"""
CI-safe tests for services/ai/tools/latency_report.py: percentile maths, the runs >= 5 gate,
the cold/warm split, a failing service being recorded without ending the run, and the
Markdown. Every HTTP call is a fake; no service, model, download or recording is involved.
"""

import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from services.ai.tools import latency_report as lr

SECRET_TRANSCRIPT = "SECRET-TRANSCRIPT-TEXT"


def write_wav(path: Path, seconds: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


class FakeHttp:
    """Stands in for lr.http_call. `delays` maps an endpoint path to elapsed seconds;
    `fail` maps a path to an error string (that endpoint then fails)."""

    def __init__(
        self,
        delays: dict[str, float] | None = None,
        fail: dict[str, str] | None = None,
        cold_extra: float = 0.0,
    ) -> None:
        self.delays = {
            "/v1/transcribe": 3.0,
            "/v1/pivot": 0.5,
            "/v1/render": 1.5,
            "/v1/moderate": 0.2,
        } | (delays or {})
        self.fail = fail or {}
        self.cold_extra = cold_extra
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._seen: set[str] = set()

    def __call__(
        self, method: str, url: str, payload: dict[str, Any] | None, timeout_s: float
    ) -> lr.HttpResult:
        self.calls.append((method, url, payload))
        base, _, path = url.partition("/v1/")
        path = "/v1/" + path if path else url[len(base) :]
        if method == "GET":
            body: dict[str, Any] = {"status": "ready", "model_version": "fake@1"}
            if ":8006" in url:
                body["decode_mode"] = "ctc"
            if ":8007" in url:
                body["decode_mode"] = "rnnt"
            return lr.HttpResult(True, 0.01, 200, body)
        elapsed = self.delays[path]
        if base not in self._seen:
            elapsed += self.cold_extra
        self._seen.add(base)
        if path in self.fail:
            return lr.HttpResult(False, elapsed, None, None, self.fail[path])
        if path == "/v1/transcribe":
            body = {
                "text": SECRET_TRANSCRIPT,
                "detected_language": "te",
                "duration_ms": elapsed * 1000 * 0.9,
                "stage_timings": [{"stage": "inference", "duration_ms": elapsed * 800}],
            }
        elif path == "/v1/pivot":
            body = {"pivot_text": "english text", "duration_ms": elapsed * 1000}
        elif path == "/v1/render":
            langs = (payload or {})["target_languages"]
            body = {
                "results": [
                    {
                        "language": lang,
                        "duration_ms": elapsed * 500,
                        "degraded": {"active": lang == "en"},
                    }
                    for lang in langs
                ]
            }
        else:
            body = {"label": "A_DEVOTIONAL", "action": "ALLOW"}
        return lr.HttpResult(True, elapsed, 200, body)

    def paths(self) -> list[str]:
        return [u.split("/v1/")[1] for _m, u, _p in self.calls if "/v1/" in u]


def clip(lang: str, tmp_path: Path, seconds: float = 30.0) -> lr.Clip:
    return lr.Clip(f"{lang}-1", lang, write_wav(tmp_path / f"{lang}.wav"), seconds, "wav_pcm16")


def runner_for(http: FakeHttp, moderation: bool = False) -> lr.Runner:
    return lr.Runner(dict(lr.DEFAULT_URLS), 30.0, http, moderation)


def build_report(
    runner: lr.Runner, clips: list[lr.Clip], runs: int, indic: str = "both", compare: bool = True
) -> str:
    return lr.render_markdown(
        runner,
        clips,
        runs,
        lr.machine_specs(),
        {},
        dict(lr.DEFAULT_URLS),
        indic,
        compare,
        [],
        False,
    )


# ---- maths ---------------------------------------------------------------------------


@pytest.mark.parametrize("q", [0, 10, 50, 90, 99, 100])
@pytest.mark.parametrize("values", [[3.0], [1.0, 2.0], [5.0, 1.0, 9.0, 3.0, 7.0], [2.5] * 4])
def test_percentile_matches_numpy(values: list[float], q: float) -> None:
    assert lr.percentile(values, q) == pytest.approx(float(np.percentile(values, q)))


def test_percentile_of_nothing_raises() -> None:
    with pytest.raises(ValueError, match="no values"):
        lr.percentile([], 50)


def test_p90_of_five_runs_is_close_to_the_worst() -> None:
    values = [10.0, 11.0, 12.0, 13.0, 20.0]
    assert lr.median(values) == 12.0
    assert lr.percentile(values, 90) == pytest.approx(13.0 + 0.6 * 7.0)


# ---- p50/p90 gate ----------------------------------------------------------------------


def test_percentiles_omitted_below_five_runs(tmp_path: Path) -> None:
    runner = runner_for(FakeHttp())
    c = clip("en", tmp_path)
    runner.run_all([c], 4, "both", True)
    report = build_report(runner, [c], 4)
    assert "p50/p90 are omitted" in report
    assert "p50 (s)" not in report
    assert "p90 (s)" not in report
    assert "NOT EVALUATED" in report
    assert "warm runs (s)" in report


def test_percentiles_shown_at_five_runs_with_caveat(tmp_path: Path) -> None:
    runner = runner_for(FakeHttp())
    c = clip("en", tmp_path)
    runner.run_all([c], 5, "both", True)
    report = build_report(runner, [c], 5)
    assert "p50 (s)" in report
    assert "p90 (s)" in report
    assert "close to the worst value" in report
    summary = lr.summarize([x for x in runner.calls if x.stage == "asr"])
    assert summary.pct(5) is not None
    assert summary.pct(4) is None


# ---- cold / warm -----------------------------------------------------------------------


def test_first_call_per_service_is_cold_and_excluded_from_warm(tmp_path: Path) -> None:
    http = FakeHttp(delays={"/v1/transcribe": 3.0}, cold_extra=10.0)
    runner = runner_for(http)
    c = clip("en", tmp_path)
    runner.run_all([c], 5, "both", True)
    asr = [x for x in runner.calls if x.stage == "asr"]
    assert [x.cold for x in asr] == [True, False, False, False, False]
    sm = lr.summarize(asr)
    assert sm.cold == pytest.approx(13.0)
    assert sm.warm == [3.0] * 4
    pct = sm.pct(5)
    assert pct is not None
    assert pct == (3.0, 3.0)
    render = lr.summarize([x for x in runner.calls if x.stage == "render"])
    assert render.cold == pytest.approx(11.5)
    assert len(render.warm) == 4


def test_cold_is_per_service_url_not_per_language(tmp_path: Path) -> None:
    runner = runner_for(FakeHttp(cold_extra=5.0))
    clips = [clip("te", tmp_path), clip("hi", tmp_path)]
    runner.run_all(clips, 2, "ctc", False)
    # One IndicConformer-CTC URL serves both languages: only its first call is cold.
    asr_cold = [(x.lang, x.cold) for x in runner.calls if x.stage == "asr"]
    assert asr_cold == [("te", True), ("hi", False), ("te", False), ("hi", False)]


def test_cold_runs_are_kept_out_of_warm_end_to_end(tmp_path: Path) -> None:
    runner = runner_for(FakeHttp(cold_extra=100.0))
    c = clip("en", tmp_path)
    runner.run_all([c], 5, "both", True)
    cold = [r for r in runner.chain_runs if r.cold]
    assert len(cold) == 1
    assert cold[0].run == 0
    assert all(r.total_s == pytest.approx(4.5) for r in runner.chain_runs if not r.cold)


# ---- chain shape ---------------------------------------------------------------------


def test_english_pivot_is_a_passthrough_and_never_calls_mt(tmp_path: Path) -> None:
    http = FakeHttp()
    runner = runner_for(http)
    c = clip("en", tmp_path)
    runner.run_all([c], 5, "both", True)
    assert "pivot" not in http.paths()
    assert not any(x.stage == "pivot" for x in runner.calls)
    report = build_report(runner, [c], 5)
    assert "passthrough" in report
    renders = [p for _m, u, p in http.calls if u.endswith("/v1/render") and p]
    assert {tuple(p["target_languages"]) for p in renders} == {("hi", "te")}


def test_telugu_runs_each_asr_variant_with_pivot_on_its_own_transcript(tmp_path: Path) -> None:
    http = FakeHttp()
    runner = runner_for(http)
    c = clip("te", tmp_path)
    runner.run_all([c], 1, "both", True)
    asr_urls = [u for _m, u, _p in http.calls if u.endswith("/v1/transcribe")]
    assert [u.split("/v1")[0] for u in asr_urls] == [
        lr.DEFAULT_URLS["indic-ctc"],
        lr.DEFAULT_URLS["indic-rnnt"],
        lr.DEFAULT_URLS["whisper"],
    ]
    pivots = [p for _m, u, p in http.calls if u.endswith("/v1/pivot") and p]
    assert len(pivots) == 3
    assert all(p["text"] == SECRET_TRANSCRIPT and p["source_language"] == "te" for p in pivots)
    renders = [p for _m, u, p in http.calls if u.endswith("/v1/render") and p]
    assert all(p["target_languages"] == ["en", "hi"] for p in renders)
    assert {r.chain for r in runner.chain_runs} == {
        "IndicConformer CTC",
        "IndicConformer RNNT",
        "faster-whisper (comparison)",
    }


def test_indic_decoder_and_whisper_compare_flags_choose_the_chains() -> None:
    assert [c.name for c in lr.chains_for("hi", "ctc", False)] == ["IndicConformer CTC"]
    assert [c.url_key for c in lr.chains_for("te", "rnnt", True)] == ["indic-rnnt", "whisper"]
    assert [c.url_key for c in lr.chains_for("en", "both", True)] == ["whisper"]


def test_moderation_is_off_by_default_and_on_with_the_flag(tmp_path: Path) -> None:
    c = clip("hi", tmp_path)
    off = FakeHttp()
    r_off = runner_for(off)
    r_off.run_all([c], 1, "ctc", False)
    assert "moderate" not in off.paths()
    on = FakeHttp()
    r_on = runner_for(on, moderation=True)
    r_on.run_all([c], 1, "ctc", False)
    assert "moderate" in on.paths()
    assert r_on.chain_runs[0].total_s == pytest.approx(3.0 + 0.5 + 1.5 + 0.2)
    assert r_off.chain_runs[0].total_s == pytest.approx(5.0)


# ---- failures ------------------------------------------------------------------------


def test_failing_pivot_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    http = FakeHttp(delays={"/v1/pivot": 7.0}, fail={"/v1/pivot": "timeout"})
    runner = runner_for(http)
    clips = [clip("hi", tmp_path), clip("en", tmp_path)]
    runner.run_all(clips, 3, "ctc", False)  # must not raise
    pivots = [x for x in runner.calls if x.stage == "pivot"]
    assert len(pivots) == 3
    assert all(not x.ok and x.elapsed_s == 7.0 and x.error == "timeout" for x in pivots)
    hi_runs = [r for r in runner.chain_runs if r.lang == "hi"]
    assert len(hi_runs) == 3
    assert all(not r.ok and r.total_s is None for r in hi_runs)
    assert not any(x.stage == "render" and x.lang == "hi" for x in runner.calls)
    assert all(r.ok for r in runner.chain_runs if r.lang == "en")  # other clips unaffected
    report = build_report(runner, clips, 3, "ctc", False)
    assert "## Failures" in report
    assert "7.00 / 7.00 / 7.00" in report
    assert "did not reach the render stage" in report


def test_failing_asr_is_a_failure_not_a_crash(tmp_path: Path) -> None:
    http = FakeHttp(delays={"/v1/transcribe": 120.0}, fail={"/v1/transcribe": "HTTP 503"})
    runner = runner_for(http)
    c = clip("en", tmp_path)
    runner.run_all([c], 5, "both", True)
    assert [x.stage for x in runner.calls] == ["asr"] * 5
    sm = lr.summarize(runner.calls)
    assert (sm.n_ok, sm.n_fail) == (0, 5)
    assert sm.fail_times == [120.0] * 5
    assert sm.pct(5) is None
    report = build_report(runner, [c], 5)
    assert "HTTP 503" in report
    assert "NOT EVALUATED" in report


def test_http_call_to_a_closed_port_fails_cleanly_with_elapsed_time() -> None:
    res = lr.http_call("POST", "http://127.0.0.1:1/v1/pivot", {"x": 1}, 2.0)
    assert not res.ok
    assert res.body is None
    assert res.error
    assert res.elapsed_s >= 0.0


# ---- target verdict -------------------------------------------------------------------


def test_verdict_met_and_missed(tmp_path: Path) -> None:
    fast = runner_for(FakeHttp(delays={"/v1/transcribe": 2.0}))
    c = clip("en", tmp_path)
    fast.run_all([c], 5, "both", True)
    lines, sentence = lr.verdict(fast.chain_runs, [c], 5)
    assert "MET" in lines[0]
    assert sentence.startswith("Target met for en / faster-whisper")
    slow = runner_for(FakeHttp(delays={"/v1/transcribe": 30.0}))
    slow.run_all([c], 5, "both", True)
    _lines, sentence = lr.verdict(slow.chain_runs, [c], 5)
    assert "missed for en / faster-whisper" in sentence


def test_verdict_not_evaluated_below_five_runs(tmp_path: Path) -> None:
    runner = runner_for(FakeHttp())
    c = clip("en", tmp_path)
    runner.run_all([c], 4, "both", True)
    _lines, sentence = lr.verdict(runner.chain_runs, [c], 4)
    assert "not evaluated" in sentence


# ---- clips -----------------------------------------------------------------------------


def test_discover_clips_by_subfolder_prefix_flag_and_default(tmp_path: Path) -> None:
    write_wav(tmp_path / "te" / "a.wav", 2.0)
    write_wav(tmp_path / "hi_note.wav")
    write_wav(tmp_path / "en-hello.wav")
    write_wav(tmp_path / "mystery.wav")
    (tmp_path / "notes.txt").write_text("not audio")
    clips, notes = lr.discover_clips([str(tmp_path)], None)
    by = {c.label: c for c in clips}
    assert set(by) == {"te-1", "hi-1", "en-1"}
    assert by["te-1"].duration_s == pytest.approx(2.0)
    assert any("1 file(s) skipped" in n for n in notes)
    clips, _ = lr.discover_clips([str(tmp_path)], "te")
    assert sorted(c.label for c in clips) == ["en-1", "hi-1", "te-1", "te-2"]
    clips, _ = lr.discover_clips([f"hi={tmp_path / 'te'}"], None)
    assert [c.lang for c in clips] == ["hi"]


def test_clip_uri_is_a_file_uri(tmp_path: Path) -> None:
    assert clip("en", tmp_path).uri.startswith("file:///")


# ---- end to end through main ---------------------------------------------------------------


def test_main_writes_a_complete_report_without_content_or_filenames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "rec"
    write_wav(audio / "te" / "private_name_one.wav", 30.0)
    write_wav(audio / "hi" / "private_name_two.wav", 30.0)
    write_wav(audio / "en" / "private_name_three.wav", 30.0)
    monkeypatch.setenv("ASR_CPU_THREADS", "7")
    monkeypatch.setenv("HF_TOKEN", "hf_must_never_appear")
    out = tmp_path / "docs" / "AI_LATENCY_REPORT.md"
    rc = lr.main(["--audio-dir", str(audio), "--runs", "5", "--out", str(out)], http=FakeHttp())
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    for heading in (
        "## Machine",
        "## Method",
        "## Results by language and stage",
        "## End-to-end total per clip",
        "## Against the proposal target",
        "## Context: earlier numbers on `main` (indicative only)",
        "## What this does not show",
    ):
        assert heading in text
    assert "ASR_CPU_THREADS=7" in text
    assert "hf_must_never_appear" not in text and "HF_TOKEN" not in text
    assert SECRET_TRANSCRIPT not in text
    assert "private_name" not in text
    for needle in ("29.4 s", "119.9 s", "79.9 s", "130.3 s", "indicative only", "p90 under 10"):
        assert needle in text
    assert "synthetic speech, on a different machine" in text
    assert "One speaker" in text and "No concurrency" in text
    assert "IndicConformer CTC" in text and "IndicConformer RNNT" in text
    assert "faster-whisper (comparison)" in text
    assert "mismatch" not in text.lower()


def test_main_warns_when_a_decoder_slot_is_running_the_other_mode(tmp_path: Path) -> None:
    audio = tmp_path / "rec"
    write_wav(audio / "en" / "x.wav")

    class Swapped(FakeHttp):
        def __call__(self, method: str, url: str, payload: Any, timeout_s: float) -> lr.HttpResult:
            res = super().__call__(method, url, payload, timeout_s)
            if method == "GET" and res.body and "decode_mode" in res.body:
                res.body["decode_mode"] = "rnnt" if ":8006" in url else "ctc"
            return res

    health = lr.probe_services(dict(lr.DEFAULT_URLS), ["indic-ctc", "indic-rnnt"], Swapped())
    assert "WARNING" in health["indic-ctc"]
    assert "WARNING" in health["indic-rnnt"]


def test_main_without_audio_returns_nonzero(tmp_path: Path) -> None:
    rc = lr.main(["--audio-dir", str(tmp_path / "nothing"), "--out", str(tmp_path / "o.md")])
    assert rc == 1
    assert not (tmp_path / "o.md").exists()


def test_main_survives_every_service_down(tmp_path: Path) -> None:
    audio = tmp_path / "rec"
    write_wav(audio / "te" / "x.wav")
    down = FakeHttp(
        fail={"/v1/transcribe": "URLError"},
    )

    def get_fails(
        method: str, url: str, payload: dict[str, Any] | None, timeout_s: float
    ) -> lr.HttpResult:
        if method == "GET":
            return lr.HttpResult(False, 0.0, None, None, "ConnectionRefusedError")
        return down(method, url, payload, timeout_s)

    out = tmp_path / "r.md"
    rc = lr.main(["--audio-dir", str(audio), "--runs", "5", "--out", str(out)], http=get_fails)
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "unreachable" in text
    assert "## Failures" in text


def test_url_flags_override_env_which_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LATENCY_MT_URL", "http://10.0.0.1:9/")
    args = lr.build_parser().parse_args(["--audio-dir", "x", "--render-url", "http://h:1"])
    urls = lr.resolve_urls(args)
    assert urls["mt"] == "http://10.0.0.1:9"
    assert urls["render"] == "http://h:1"
    assert urls["whisper"] == lr.DEFAULT_URLS["whisper"]
