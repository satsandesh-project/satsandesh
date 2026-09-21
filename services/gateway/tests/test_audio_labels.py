"""Tests for GET /audio-labels/{label} — Phase 8's UI-label accessibility
endpoint.

Step 0 finding: services/ai/'s mock server has no generic "read this UI
label aloud" endpoint — only content-pipeline TTS via POST /v1/render,
which expects arbitrary pivot text and target languages for a *message*,
not a fixed catalog of UI strings. Per the plan's fallback, this is a small
stdlib-only (`wave` module) stub entirely inside services/gateway/ — no
proxying, no changes to services/ai/ — documented as a Month 2 placeholder
for real TTS.

Written before app/audio_labels.py exists — collecting this file succeeds,
but every test is expected to fail with a 404 (no such route registered on
app.main.app) until the implementation lands.

No DB involved, so this uses its own lightweight `client` fixture rather
than tests/conftest.py's `client` (which requires a live db_session) — same
pattern tests/test_health.py and tests/test_auth.py already establish for
DB-free routes.
"""

import pytest
from fastapi.testclient import TestClient

from app.audio_labels import _LABELS
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_audio_label_returns_audio_bytes(client):
    response = client.get("/audio-labels/send_button", params={"lang": "en"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert len(response.content) > 0


def test_audio_label_unknown_label_returns_404(client):
    response = client.get("/audio-labels/nonexistent_label", params={"lang": "en"})

    assert response.status_code == 404


def test_audio_label_unknown_lang_falls_back_to_en(client):
    response = client.get("/audio-labels/send_button", params={"lang": "xx"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert len(response.content) > 0


def test_audio_label_no_lang_defaults_to_en(client):
    response = client.get("/audio-labels/send_button")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"


def test_audio_label_te_returns_audio_bytes(client):
    # Regression for PR #45's review: clients/elder-app/'s only two
    # languages are en/te, but this catalog only ever had en/hi -- every
    # Telugu-preference elder silently got _FALLBACK_LANG's English clip.
    # _synth_stub doesn't vary its output by text (still a placeholder
    # tone), so this only proves "te" resolves without a 404/error, not
    # that the audio differs from "en" -- see test_every_label_has_telugu
    # below for the part that actually catches a missing translation.
    response = client.get("/audio-labels/send_button", params={"lang": "te"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert len(response.content) > 0


def test_every_label_has_telugu():
    missing = [label for label, texts in _LABELS.items() if "te" not in texts]
    assert missing == []
