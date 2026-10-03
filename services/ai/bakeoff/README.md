# ASR bake-off — sample recording (`services/ai/bakeoff/`)

This directory supports the Week 5 bake-off: comparing ASR model quality and
latency on **real** speech, not the synthetic tone fixture used by
`services/ai/speech/tests/`. It is not part of the transcribe service itself.

## Recording a sample

```bash
cd services/ai
./.venv/Scripts/python.exe -m pip install -e ".[tools]"   # once, for sounddevice/soundfile
./.venv/Scripts/python.exe tools/record_sample.py --label telugu_01 --seconds 30
```

- `--label` names the file: saved to `bakeoff/samples/<label>.wav`.
- `--seconds` defaults to 30, override as needed.
- Output is mono, 16-bit PCM, 16kHz — the exact shape
  `services/ai/speech/engine.py`'s `decode_wav_pcm16()` already expects, so a
  bake-off runner can feed these files straight into the transcribe service
  with no ffmpeg conversion step.
- The script refuses to overwrite an existing file for the same label — pick
  a new label or delete the old file yourself first.
- A recording that ran short (device hiccup, etc.) prints an explicit
  `WARNING` with the actual captured duration — it is never silently saved as
  if it were the full requested length.

## What samples are actually needed

**5–10 real recordings, roughly 30 seconds each, in Telugu**, covering a
spread of conditions:

- at least one in a quiet room
- at least one with background noise
- at least one that naturally mixes in English words (code-mixing)
- an elderly speaker, if one is available (not required if not)

The point is coverage across realistic conditions the ASR model will actually
face, not a large corpus — quality/spread over quantity.

## Privacy — these files never leave this machine via git

A voice recording of a real person is personal data. Everything under
`bakeoff/samples/` is gitignored (`bakeoff/samples/.gitignore`) — **never
tracked, never staged, never committed, under any circumstance**, including a
future "just commit everything" request. If a sample needs to be shared with
a teammate for evaluation, that is a deliberate, manual action you take
yourself (e.g. a direct file transfer) — not something git does
automatically, and not something to route through this repository at all.
