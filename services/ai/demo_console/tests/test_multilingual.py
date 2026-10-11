"""Tests for English speech, per-language ASR routing, the reply stage for en/te/hi,
the page-language (i18n) tables and the launcher's faster-whisper child.

CI-safe: no models, no microphone, no network. Upstream HTTP is faked with
httpx.MockTransport exactly like test_demo_console.py / test_full_pipeline.py.
"""

from __future__ import annotations

import dataclasses
import json
import re
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from services.ai.demo_console import app as console
from services.ai.demo_console import start_demo

from .test_full_pipeline import _fake_upstream, _render_response, make_wav

STATIC = Path(console.STATIC_DIR)
I18N = STATIC / "i18n"
PAGE_LANGS = ("en", "te", "hi")

# Distinct fake ports so a handler can tell which upstream was called.
INDIC_PORT, EN_PORT, MT_PORT, RENDER_PORT = 18002, 18007, 18004, 18006


@pytest.fixture
def client() -> TestClient:
    return TestClient(console.app)


@pytest.fixture
def upstreams(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list]:
    """Point every upstream at a fake port and record which ones were called."""
    for attr, port in [
        ("ASR", INDIC_PORT),
        ("ASR_EN", EN_PORT),
        ("MT", MT_PORT),
        ("RENDER", RENDER_PORT),
    ]:  # keep each upstream's real key/messages; only the address changes
        real = getattr(console, attr)
        monkeypatch.setattr(
            console, attr, dataclasses.replace(real, base_url=f"http://127.0.0.1:{port}")
        )
    monkeypatch.setattr(console, "ASR_EN_ENABLED", True)
    rec = tmp_path / "rec.wav"
    rec.write_bytes(make_wav(1.0))
    monkeypatch.setattr(console, "RECORDING_PATH", rec)
    out = tmp_path / "render_out"
    out.mkdir()
    monkeypatch.setattr(console, "RENDER_OUTPUT_DIR", out)

    calls: dict = {"asr": [], "asr_en": [], "mt": [], "render": [], "override": []}
    by_port = {INDIC_PORT: "asr", EN_PORT: "asr_en", MT_PORT: "mt", RENDER_PORT: "render"}

    def handler(req: httpx.Request) -> httpx.Response:
        if calls["override"]:  # a test replacing the fakes (patches cannot be nested)
            return calls["override"][0](req)
        which = by_port[req.url.port]
        body = json.loads(req.content) if req.content else {}
        calls[which].append((req.url.path, body))
        if which in ("asr", "asr_en"):
            return httpx.Response(
                200,
                json={
                    "text": "hello there" if which == "asr_en" else "నమస్కారం",
                    "detected_language": body["language_hint"],
                    "model_version": "whisper-x" if which == "asr_en" else "conformer-x",
                    "duration_ms": 100.0,
                    "stage_timings": [{"stage": "inference", "duration_ms": 90.0}],
                },
            )
        if which == "mt":
            return httpx.Response(
                200,
                json={"pivot_text": "hello (from mt)", "model_version": "mt-x", "duration_ms": 5},
            )
        lang = body["target_languages"][0]
        path = out / f"render-{uuid.uuid4().hex}-{lang}.wav"
        path.write_bytes(make_wav(1.0))
        return httpx.Response(
            200, json=_render_response("అనువాదం", path.resolve().as_uri(), lang=lang)
        )

    _fake_upstream(monkeypatch, handler)
    return calls


# --------------------------------------------------------------------------
# Routing: ASR URL by declared language
# --------------------------------------------------------------------------


def test_routing_table(upstreams) -> None:
    assert console.asr_upstream_for("te") is console.ASR
    assert console.asr_upstream_for("hi") is console.ASR
    assert console.asr_upstream_for("en") is console.ASR_EN
    assert console.ASR is not console.ASR_EN


@pytest.mark.parametrize("lang", ["te", "hi"])
def test_indic_note_goes_to_indicconformer_with_hint(client, upstreams, lang) -> None:
    resp = client.post("/pipeline/transcribe", json={"source_language": lang})
    assert resp.status_code == 200, resp.text
    assert len(upstreams["asr"]) == 1 and not upstreams["asr_en"]
    path, body = upstreams["asr"][0]
    assert path == "/v1/transcribe"
    assert body["language_hint"] == lang
    assert resp.json()["model_version"] == "conformer-x"


def test_english_note_goes_to_faster_whisper_with_hint(client, upstreams) -> None:
    resp = client.post("/pipeline/transcribe", json={"source_language": "en"})
    assert resp.status_code == 200, resp.text
    assert len(upstreams["asr_en"]) == 1 and not upstreams["asr"]
    assert upstreams["asr_en"][0][1]["language_hint"] == "en"
    assert resp.json()["text"] == "hello there"
    assert resp.json()["model_version"] == "whisper-x"  # the page labels the producing model


def test_asr_en_off_gives_a_calm_message_and_calls_nothing(client, upstreams, monkeypatch) -> None:
    monkeypatch.setattr(console, "ASR_EN_ENABLED", False)
    resp = client.post("/pipeline/transcribe", json={"source_language": "en"})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert "faster-whisper" in err["title"] and "English" in err["title"]
    assert err["code"] == "asr_en_unavailable"
    assert "Traceback" not in resp.text
    assert not upstreams["asr"] and not upstreams["asr_en"]


def test_asr_en_off_leaves_typed_english_and_indic_working(client, upstreams, monkeypatch) -> None:
    monkeypatch.setattr(console, "ASR_EN_ENABLED", False)
    typed = client.post(
        "/pipeline/translate", json={"text": "How are you?", "source_language": "en"}
    )
    assert typed.status_code == 200 and typed.json()["text"] == "How are you?"
    reply = client.post("/pipeline/render", json={"text": "Fine", "target_language": "te"})
    assert reply.status_code == 200
    assert client.post("/pipeline/transcribe", json={"source_language": "te"}).status_code == 200


def test_english_service_down_gives_the_same_calm_message(client, upstreams, monkeypatch) -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        if req.url.port == EN_PORT:
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(500)

    upstreams["override"].append(refuse)
    resp = client.post("/pipeline/transcribe", json={"source_language": "en"})
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["code"] == "asr_en_unavailable"
    assert "faster-whisper" in err["title"]
    assert "Try again" in err["message"]


def test_page_language_cannot_change_the_asr_hint(client, upstreams) -> None:
    """The page language is a client-only setting; a stray field never reroutes speech."""
    resp = client.post(
        "/pipeline/transcribe", json={"source_language": "en", "page_language": "te", "lang": "hi"}
    )
    assert resp.status_code == 200
    assert upstreams["asr_en"][0][1]["language_hint"] == "en" and not upstreams["asr"]
    resp = client.post(
        "/pipeline/transcribe", json={"source_language": "te", "page_language": "en"}
    )
    assert upstreams["asr"][0][1]["language_hint"] == "te"


def test_unsupported_speech_language_still_rejected(client, upstreams) -> None:
    resp = client.post("/pipeline/transcribe", json={"source_language": "ta"})
    assert resp.status_code == 422 and resp.json()["error"]["title"] == "Unsupported language"


# --------------------------------------------------------------------------
# English path skips MT
# --------------------------------------------------------------------------


def test_english_text_is_the_pivot_and_mt_is_not_called(client, upstreams) -> None:
    resp = client.post(
        "/pipeline/translate", json={"text": " How are you? ", "source_language": "en"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "How are you?"
    assert body["skipped"] is True and body["model_version"] is None
    assert upstreams["mt"] == []


@pytest.mark.parametrize("lang", ["te", "hi"])
def test_indic_text_calls_mt(client, upstreams, lang) -> None:
    resp = client.post("/pipeline/translate", json={"text": "నమస్కారం", "source_language": lang})
    assert resp.status_code == 200
    assert resp.json()["text"] == "hello (from mt)" and resp.json()["skipped"] is False
    assert len(upstreams["mt"]) == 1
    assert upstreams["mt"][0][1] == {"text": "నమస్కారం", "source_language": lang}


# --------------------------------------------------------------------------
# Reply stage
# --------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["te", "hi"])
def test_reply_in_indic_calls_render_and_returns_text_and_audio(client, upstreams, lang) -> None:
    resp = client.post("/pipeline/render", json={"text": "Take care", "target_language": lang})
    assert resp.status_code == 200
    body = resp.json()
    assert upstreams["render"][0][1] == {"pivot_text": "Take care", "target_languages": [lang]}
    assert body["text"] and body["audio_url"].startswith("/pipeline/audio/")
    assert body["audio_url"].endswith(f"-{lang}.wav")
    assert body["degraded"]["active"] is False


def test_reply_in_english_without_a_voice_is_text_only_and_render_is_not_called(
    client, upstreams, monkeypatch
) -> None:
    monkeypatch.setattr(console, "EN_VOICE_ENABLED", False)
    resp = client.post("/pipeline/render", json={"text": "Take care", "target_language": "en"})
    assert resp.status_code == 200
    body = resp.json()
    assert upstreams["render"] == []
    assert body["text"] == "Take care" and body["language"] == "en"
    assert body["audio_url"] is None and body["audio_duration_ms"] is None
    assert body["degraded"]["active"] is True and body["degraded"]["reason"] == "NO_ENGLISH_VOICE"
    assert body["skipped_render"] is True


def test_degraded_render_result_is_shown_not_an_error(client, upstreams, monkeypatch) -> None:
    degraded = {"active": True, "reason": "TTS_SKIPPED", "detail": "VRAM"}
    upstreams["override"].append(
        lambda req: httpx.Response(
            200, json=_render_response("అనువాదం", "", lang="hi", degraded=degraded)
        )
    )
    resp = client.post("/pipeline/render", json={"text": "Hi", "target_language": "hi"})
    assert resp.status_code == 200
    assert resp.json()["audio_url"] is None and resp.json()["degraded"]["active"] is True


# --------------------------------------------------------------------------
# /health/deps
# --------------------------------------------------------------------------


def test_health_deps_reports_english_asr(client, upstreams, monkeypatch) -> None:
    upstreams["override"].append(
        lambda req: httpx.Response(
            200, json={"status": "ready", "model_version": f"m{req.url.port}"}
        )
    )
    body = client.get("/health/deps").json()
    assert body["asr_en"]["state"] == "ready" and body["asr_en"]["model"] == f"m{EN_PORT}"
    monkeypatch.setattr(console, "ASR_EN_ENABLED", False)
    assert client.get("/health/deps").json()["asr_en"]["state"] == "off"


# --------------------------------------------------------------------------
# i18n tables
# --------------------------------------------------------------------------


def _table(lang: str) -> dict[str, str]:
    return json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))


def _placeholders(s: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", s))


def test_every_page_language_has_a_table_and_the_switcher_matches() -> None:
    files = {p.stem for p in I18N.glob("*.json")}
    assert files == set(PAGE_LANGS)
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert set(re.findall(r'data-page-lang="(\w+)"', html)) == set(PAGE_LANGS)


def test_no_key_is_missing_or_extra_in_any_language() -> None:
    keys = {lang: set(_table(lang)) for lang in PAGE_LANGS}
    for lang in PAGE_LANGS:
        assert keys[lang] == keys["en"], (
            f"{lang}: missing {sorted(keys['en'] - keys[lang])}, "
            f"extra {sorted(keys[lang] - keys['en'])}"
        )


def test_no_empty_strings_and_placeholders_agree() -> None:
    en = _table("en")
    for lang in PAGE_LANGS:
        for key, value in _table(lang).items():
            assert isinstance(value, str) and value.strip(), f"{lang}.{key} is empty"
            assert _placeholders(value) == _placeholders(en[key]), f"{lang}.{key} placeholders"


def test_every_key_the_page_uses_exists() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-[a-z]+)?="(\w+)"', html))
    used |= set(re.findall(r'\bt\(\s*"(\w+)"', html))
    used |= set(re.findall(r'\bkey:\s*"(err_\w+)"', html))
    used |= set(re.findall(r'setStatus\(\s*"(\w+)"', html))
    used |= set(re.findall(r'(?:running|text):\s*"(run_\w+)"', html))
    prefixes = {k for k in used if k.endswith("_")}  # built at run time: t("dep_" + key)
    used -= prefixes
    assert used, "the page should reference i18n keys"
    en = _table("en")
    # err_x keys are looked up as err_x_title / err_x_msg
    wanted = {f"{k}_title" if k.startswith("err_") else k for k in used}
    wanted |= {f"{k}_msg" for k in used if k.startswith("err_")}
    assert wanted <= set(en), sorted(wanted - set(en))
    assert prefixes <= {"dep_", "lang_", "step_", "svc_"}, prefixes


def test_run_time_key_families_are_complete() -> None:
    en = _table("en")
    families = {
        "dep_": ["asr", "asr_en", "mt", "render"],
        "svc_": ["asr", "asr_en", "mt", "render"],
        "step_": ["record", "upload", "transcribe", "translate", "render", "skipped"],
        "lang_": list(PAGE_LANGS),
    }
    for prefix, suffixes in families.items():
        for suffix in suffixes:
            assert prefix + suffix in en, prefix + suffix
    for state in ("ready", "warming", "down", "off", "unknown", "checking"):
        assert f"state_{state}" in en
    for key in (
        "lat_record",
        "lat_upload",
        "lat_asr_decode",
        "lat_asr_inference",
        "lat_asr_postprocess",
        "lat_asr_total",
        "lat_asr_round",
        "lat_mt_total",
        "lat_mt_round",
        "lat_render_total",
        "lat_render_round",
    ):
        assert key in en, key


def test_every_error_code_the_console_can_send_has_wording() -> None:
    en = _table("en")
    source = Path(console.__file__).read_text(encoding="utf-8")
    codes = set(re.findall(r'code="(\w+)"', source))
    assert {"asr_en_unavailable", "silent", "no_speech", "upstream_down"} <= codes
    for code in codes:
        assert f"err_{code}_title" in en and f"err_{code}_msg" in en, code


def test_speech_language_names_exist_in_every_page_language() -> None:
    for lang in PAGE_LANGS:
        table = _table(lang)
        assert all(f"lang_{code}" in table for code in PAGE_LANGS)


def test_native_review_file_lists_every_te_and_hi_string() -> None:
    """Nothing in te/hi is presented as verified: every key is in the review table."""
    review = (I18N / "NEEDS_NATIVE_REVIEW.md").read_text(encoding="utf-8")
    rows = {
        (m.group(1), m.group(2))
        for m in re.finditer(r"^\|\s*`(\w+)`\s*\|\s*(Telugu|Hindi)\s*\|", review, re.MULTILINE)
    }
    for lang, name in (("te", "Telugu"), ("hi", "Hindi")):
        for key in _table(lang):
            assert (key, name) in rows, f"{lang}.{key} is not in NEEDS_NATIVE_REVIEW.md"


def test_switcher_html_lang_and_transcript_lang_wiring() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<html lang="en"', html)
    assert "document.documentElement.lang" in html
    assert re.search(r"setAttribute\(\s*\"lang\"", html)  # lang= on transcript blocks


def test_page_works_without_localstorage_and_without_network() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r"try\s*\{[^}]*localStorage", html)
    sources = [html] + [p.read_text(encoding="utf-8") for p in STATIC.rglob("*.json")]
    for text in sources:
        assert not re.search(r"""(src|href)\s*=\s*["']\s*(https?:)?//""", text)
        assert not re.search(r"url\(\s*[\"']?(https?:)?//", text)
        assert "@import" not in text
        for banned in ("googleapis", "gstatic", "cdn.", "unpkg", "jsdelivr", "cdnjs"):
            assert banned not in text, banned
    assert "Nirmala UI" in html


# --------------------------------------------------------------------------
# Launcher: faster-whisper English child
# --------------------------------------------------------------------------


def test_launcher_adds_faster_whisper_on_8007() -> None:
    services = start_demo.build_services("indicconformer", asr_en="whisper")
    assert [s.role for s in services] == ["asr", "asr_en", "mt", "render", "console"]
    en = services[1]
    assert en.module == "speech.app" and en.port == 8007
    assert en.extra_env["ASR_PORT"] == "8007"
    assert en.ready_path == "/health/ready" and not en.needs_token
    assert [s.port for s in services] == [8002, 8007, 8004, 8006, 8005]


def test_launcher_asr_en_off_starts_no_english_child() -> None:
    services = start_demo.build_services("indicconformer", asr_en="off")
    assert [s.role for s in services] == ["asr", "mt", "render", "console"]


def test_launcher_whisper_primary_serves_english_too() -> None:
    services = start_demo.build_services("whisper", asr_en="whisper")
    assert [s.role for s in services].count("asr_en") == 0  # one whisper, no duplicate process
    env = start_demo.child_env(services[-1], {})
    assert env["DEMO_ASR_EN_PORT"] == "8002"


def test_launcher_default_asr_en_flag_is_whisper_and_validated() -> None:
    assert start_demo.parse_args([]).asr_en == "whisper"
    assert start_demo.parse_args(["--asr-en", "off"]).asr_en == "off"
    with pytest.raises(SystemExit):
        start_demo.parse_args(["--asr-en", "nope"])


def test_launcher_child_env_tells_the_console_about_english_asr() -> None:
    on = start_demo.build_services("indicconformer", asr_en="whisper")
    off = start_demo.build_services("indicconformer", asr_en="off")
    assert start_demo.child_env(on[-1], {})["DEMO_ASR_EN"] == "whisper"
    assert start_demo.child_env(on[-1], {})["DEMO_ASR_EN_PORT"] == "8007"
    assert start_demo.child_env(off[-1], {})["DEMO_ASR_EN"] == "off"
    # A stale ASR_PORT in the shell must not move the English child off 8007.
    assert start_demo.child_env(on[1], {"ASR_PORT": "9999"})["ASR_PORT"] == "8007"
    assert start_demo.child_env(on[0], {"ASR_PORT": "9999"})["ASR_PORT"] == "8002"
    env = start_demo.child_env(on[1], {"HF_TOKEN": "hf_x"})
    assert env["HF_HUB_OFFLINE"] == "1" and env["TRANSFORMERS_OFFLINE"] == "1"


def test_launcher_warns_when_port_8007_is_not_faster_whisper() -> None:
    warn = start_demo.asr_en_identity_warning("ai4bharat/indic-conformer-x", 8007)
    assert warn and "WARNING" in warn and "8007" in warn
    assert start_demo.asr_en_identity_warning("faster-whisper-small-int8@1.0", 8007) is None
    assert start_demo.asr_en_identity_warning(None, 8007) is None


def test_default_launch_starts_five_processes_and_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("HF_TOKEN", "hf_FAKE_TOKEN_for_tests_0123456789")
    monkeypatch.setattr(start_demo, "LOG_DIR", tmp_path)
    started: dict[int, dict] = {}

    class FakeProc:
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        started[int(cmd[cmd.index("--port") + 1])] = kwargs["env"]
        return FakeProc()

    def fake_status(url: str, timeout: float = 2.0):
        return 200 if int(url.split(":")[2].split("/")[0]) in started else None

    def stop_after_ready(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(start_demo.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(start_demo, "_http_status", fake_status)
    monkeypatch.setattr(start_demo, "_python_exe", lambda: "python")
    monkeypatch.setattr(start_demo.time, "sleep", stop_after_ready)
    assert start_demo.main(["--no-browser"]) == 0
    out = capsys.readouterr()
    assert set(started) == {8002, 8004, 8005, 8006, 8007}
    assert "hf_FAKE_TOKEN" not in out.out + out.err
    assert started[8007]["ASR_PORT"] == "8007"
    assert started[8005]["DEMO_ASR_EN"] == "whisper"
