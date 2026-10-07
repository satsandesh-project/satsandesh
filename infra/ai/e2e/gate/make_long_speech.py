"""A ~30 second spoken note per language, from DIFFERENT sentences. Runs INSIDE the render container
(python on stdin), like real_make_speech.py.

Why this exists: a first attempt looped one 5 s sentence six times to reach 30 s, and the ASR
transcribed the whole thing as about one short phrase (16 characters), so its timing said nothing
about 30 s of speech. Distinct sentences cannot be collapsed that way.

Each English sentence is rendered to Hindi and Telugu by the real render service (MT + Piper) and
the per-sentence WAVs are concatenated, with a short pause, until the note reaches TARGET_S.
Synthetic speech, not a person: it says nothing about accuracy on an elder's voice.
Prints one JSON line: {"hi": {"file": ..., "seconds": ..., "sentences": ...}, "te": {...}}.
"""

import json
import urllib.request
import wave

TARGET_S = 30.0
PAUSE_S = 0.3
SENTENCES = [
    "Namaste everyone, this is the weekly announcement from the temple committee.",
    "This Sunday evening at six o'clock we will hold the bhajan in the main hall.",
    "After the bhajan there will be a short talk about the importance of service.",
    "Please bring your own water bottle and a small mat to sit on.",
    "Tea and prasadam will be served at eight o'clock in the garden.",
    "Elders who need a ride should tell the volunteers by Friday evening.",
    "We are grateful to everyone who helped with the decoration this week.",
    "The children's group will sing two songs before the main program begins.",
    "If you cannot come, please send your blessings and we will remember you.",
    "The library will be open for one hour after the prasadam.",
    "Next week we will celebrate the festival with a special puja at dawn.",
    "Thank you all for your love, your time and your support.",
]


def render(sentence):
    body = json.dumps({"pivot_text": sentence, "target_languages": ["hi", "te"]}).encode()
    req = urllib.request.Request(
        "http://localhost:8005/v1/render", data=body, headers={"Content-Type": "application/json"}
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
    return {r["language"]: r["audio"]["uri"].rsplit("/", 1)[-1] for r in resp["results"]}


def read(path):
    with wave.open(path) as w:
        return w.getparams(), w.readframes(w.getnframes())


parts = {"hi": [], "te": []}
seconds = {"hi": 0.0, "te": 0.0}
used = {"hi": 0, "te": 0}
for sentence in SENTENCES:
    if all(seconds[lang] >= TARGET_S for lang in parts):
        break
    files = render(sentence)
    for lang in parts:
        if seconds[lang] >= TARGET_S:
            continue
        params, frames = read("/render-output/" + files[lang])
        parts[lang].append((params, frames))
        seconds[lang] += params.nframes / params.framerate + PAUSE_S
        used[lang] += 1

out = {}
for lang, chunks in parts.items():
    params = chunks[0][0]
    silence = b"\x00" * (2 * int(params.framerate * PAUSE_S) * params.nchannels)
    path = "/render-output/long-%s.wav" % lang
    with wave.open(path, "wb") as w:
        w.setparams(params)
        for _params, frames in chunks:
            w.writeframes(frames + silence)
    out[lang] = {
        "file": "long-%s.wav" % lang,
        "seconds": round(seconds[lang], 1),
        "sentences": used[lang],
    }
print(json.dumps(out))
