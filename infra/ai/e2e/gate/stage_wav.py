"""Puts the synthesized Telugu WAV into the gate stack's media store (as the elder) so the browser
can fetch it SAME-ORIGIN through Caddy. This is only a way to hand the sample to the browser; the
recording and the upload of the voice note the pipeline processes are done by the browser itself."""

import json
import wave

import httpx

state = json.load(open("/state/gate_state.json", encoding="utf-8"))
raw = open("/samples/te.wav", "rb").read()
with wave.open("/samples/te.wav") as w:
    ms = round(1000 * w.getnframes() / w.getframerate())
    print(
        "wav:",
        w.getnchannels(),
        "ch",
        w.getframerate(),
        "Hz",
        8 * w.getsampwidth(),
        "bit",
        ms,
        "ms",
    )
r = httpx.post(
    f"http://caddy:80/media?format=wav_pcm16&duration_ms={ms}",
    content=raw,
    headers={"Authorization": f"Bearer {state['elder']['token']}"},
    timeout=60,
)
print(r.status_code, json.dumps(r.json()))
