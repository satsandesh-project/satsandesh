"""
Real-model test: loads the actual gated IndicConformer model and transcribes a real
Telugu recording. Skipped cleanly unless HF_TOKEN is set (same pattern as render's real
MT test). Asserts Telugu SCRIPT, not exact text -- there is no verified ground truth yet.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import services.ai.speech_indicconformer.app as ic_app
from contracts.ai.transcribe import TranscribeResponse
from fastapi.testclient import TestClient

SAMPLES = Path(__file__).resolve().parents[2] / "bakeoff" / "samples"
TELUGU_RANGE = range(0x0C00, 0x0C80)


def _telugu_ratio(text: str) -> float:
    letters = [c for c in text if not c.isspace()]
    return sum(ord(c) in TELUGU_RANGE for c in letters) / max(len(letters), 1)


@pytest.fixture(scope="module")
def real_client():
    if not os.environ.get("HF_TOKEN", "").strip():
        pytest.skip("HF_TOKEN not set: the real IndicConformer model test needs the gated model")
    try:
        with TestClient(ic_app.app) as c:
            yield c
    except OSError as exc:
        pytest.skip(f"real IndicConformer model not accessible with this HF_TOKEN: {exc!s:.200}")


def test_real_model_transcribes_mobile_01_as_telugu(real_client: TestClient) -> None:
    wav = SAMPLES / "mobile_01.wav"
    if not wav.is_file():
        pytest.skip(f"sample missing (gitignored, copy it in): {wav}")
    uri = "file:///" + str(wav).replace("\\", "/")
    resp = real_client.post(
        "/v1/transcribe",
        json={
            "audio": {
                "uri": uri,
                "format": "wav_pcm16",
                "duration_ms": 14530,
                "sample_rate_hz": 16000,
            },
            "language_hint": "te",
        },
    )
    assert resp.status_code == 200, resp.text
    parsed = TranscribeResponse.model_validate(resp.json())
    assert parsed.text.strip() != ""
    assert _telugu_ratio(parsed.text) > 0.8, parsed.text
    assert parsed.detected_language.value == "te"
