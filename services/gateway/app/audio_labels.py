"""Week 4 Phase 8: `GET /audio-labels/{label}` — a small accessibility
endpoint that reads short UI-label strings aloud, for low-literacy elders
navigating the app.

Step 0 finding: `services/ai/`'s mock exposes no generic "read this UI
label aloud" endpoint — only `POST /v1/render`, a content-pipeline call
expecting arbitrary pivot text and a real translate+TTS pipeline behind it,
not a fixed catalog of UI strings. Proxying to it for five static labels
would mean inventing pivot text and paying a whole pipeline call for what's
really a static asset, so this stays entirely inside `services/gateway/`:
`_synth_stub` below generates a minimal, valid WAV via the stdlib `wave`
module — a placeholder tone, not real speech — good enough to wire up the
client-side "tap to hear this button" UX now. Swap `_synth_stub` for a real
call into `services/ai/`'s render pipeline (or a pre-rendered asset store)
in Month 2 without changing this route's shape.
"""

import io
import math
import struct
import wave

from fastapi import APIRouter, HTTPException, Response

router = APIRouter()

_SAMPLE_RATE_HZ = 8000
_TONE_HZ = 440.0
_DURATION_S = 0.3

# label -> {lang -> text}. _synth_stub doesn't actually speak `text` today
# (a placeholder tone, not real TTS) — the catalog carries it anyway so
# wiring in a real TTS call later is a one-line change to _synth_stub, not
# a rewrite of this catalog or the route around it.
_LABELS: dict[str, dict[str, str]] = {
    "send_button": {"en": "Send", "hi": "भेजें"},
    "back": {"en": "Back", "hi": "वापस"},
    "new_message": {"en": "New message", "hi": "नया संदेश"},
    "circle": {"en": "Circle", "hi": "मंडली"},
    "settings": {"en": "Settings", "hi": "सेटिंग्स"},
}

_FALLBACK_LANG = "en"


def _synth_stub(text: str, lang: str) -> bytes:
    """Placeholder for real TTS (Month 2): a short sine-tone WAV, not
    speech. Accepts `text`/`lang` now so the call site never has to change
    shape once this calls a real pipeline instead."""
    frame_count = int(_SAMPLE_RATE_HZ * _DURATION_S)
    samples = [
        int(32767 * math.sin(2 * math.pi * _TONE_HZ * i / _SAMPLE_RATE_HZ))
        for i in range(frame_count)
    ]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE_HZ)
        wav_file.writeframes(struct.pack(f"<{frame_count}h", *samples))
    return buffer.getvalue()


@router.get("/audio-labels/{label}")
def get_audio_label(label: str, lang: str = _FALLBACK_LANG) -> Response:
    texts = _LABELS.get(label)
    if texts is None:
        raise HTTPException(status_code=404, detail="Unknown audio label")

    # Never 404 on an unrecognized lang — fall back to English rather than
    # fail a request whose only problem is a language this catalog doesn't
    # have yet.
    resolved_lang = lang if lang in texts else _FALLBACK_LANG
    audio_bytes = _synth_stub(texts[resolved_lang], resolved_lang)

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )
