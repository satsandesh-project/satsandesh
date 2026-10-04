"""Makes the Hindi and Telugu "spoken" samples for run_proof_real.sh.

Runs INSIDE the render container (python stdin). The real render service turns
one English sentence into Hindi and Telugu text plus Piper speech; the WAV files
it writes are what the proof then plays into the pipeline as if an elder had
spoken them. Prints one JSON line: {"hi": "<file>", "te": "<file>", ...}.

This is synthetic speech from the same voices the pipeline renders with, not a
person on a microphone, so it is evidence that the stages connect, NOT evidence
about accuracy on an elder's voice (the proof says so too).
"""

import json
import urllib.request

PIVOT = "There is a bhajan at the temple at seven this evening. Everyone is welcome."
body = json.dumps({"pivot_text": PIVOT, "target_languages": ["hi", "te"]}).encode()
req = urllib.request.Request(
    "http://localhost:8005/v1/render", data=body, headers={"Content-Type": "application/json"}
)
resp = json.loads(urllib.request.urlopen(req, timeout=300).read().decode())
out = {}
for r in resp["results"]:
    out[r["language"]] = r["audio"]["uri"].rsplit("/", 1)[-1]
    out[r["language"] + "_text"] = r["text"]
print(json.dumps(out, ensure_ascii=False))
