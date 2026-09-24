# Demo console (`services/ai/demo_console/`)

A small local web page that runs the **real** AI pipeline end to end, for live
demos and manual testing:

    microphone  ->  real ASR (services/ai/speech/, :8002)  ->  real MT pivot (services/ai/mt/, :8004)

**This is a demo/testing harness, not the product UI.** The real user-facing app
is `clients/elder-app/` (owned by M1). The console talks to the ASR and MT
services only over their public HTTP APIs (`POST /v1/transcribe`, `POST /v1/pivot`),
the same way the gateway eventually will. It never imports their engines.

## Run it (the one command)

From the repo root:

```powershell
services\ai\.venv\Scripts\python.exe services\ai\demo_console\start_demo.py
```

This starts the ASR service, the MT service and the console, waits for all three
to report ready, prints `DEMO READY: http://127.0.0.1:8005/` and opens the
browser. Leave that window open. Ctrl+C stops only the processes this command
started.

- A service already running on its port is **reused**, not restarted. If one
  service dies mid-demo, run the same command again and only the missing one
  is started.
- Logs for each process go to `demo_console/_logs/` (gitignored).
- `--no-browser` prints the URL without opening it.
- Children run with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, so models
  load only from the local Hugging Face cache. If the stack comes up ready, the
  demo needs no network. Required cache entries:
  `models--Systran--faster-whisper-small` and
  `models--ai4bharat--indictrans2-indic-en-dist-200M`.
- Needs the `tools` (sounddevice/soundfile) and `mt` extras installed in
  `services/ai/.venv`.

## What the page shows

1. Language toggle: Telugu (default) / Hindi, sent as the ASR `language_hint`
   and the MT `source_language`.
2. **Record (7s)** with a live countdown. The button stays disabled until the
   run finishes, so a double click can't start a second run.
3. Step-by-step status (Record -> Transcribe -> Translate) with a live
   elapsed-seconds counter, so a slow CPU inference never looks frozen.
4. Panels: **Transcript** (source language), **English translation**, and
   **Latency per stage**. The latency figures come from the services' own
   responses (ASR `stage_timings` and `duration_ms`, MT `duration_ms`) plus the
   console-measured round trip. Nothing is estimated.
5. A status line showing whether ASR and MT are ready, warming up or not running.

On any failure (no microphone, silent recording, a service not running, a model
still warming up, a timeout, no speech recognised), the page shows a short,
specific message and a **Try again** button. No page reload or restart is needed.

## How it's built

- `app.py` is FastAPI on port **8005** (8001 mock, 8002 speech, 8003
  moderation and 8004 MT were taken). It reads the ASR/MT ports from
  `speech/settings.py` and `mt/settings.py`, so env overrides there are
  honoured. Endpoints:
  - `GET /health` is liveness. `GET /health/deps` reports ASR/MT readiness.
  - `POST /pipeline/record` records 7s with
    `services/ai/tools/record_sample.py`'s `record_to_file()` (the same code path
    as the bake-off CLI) to `_last_recording.wav` (overwritten, gitignored).
  - `POST /pipeline/transcribe` sends `{source_language}` to the ASR service.
  - `POST /pipeline/translate` sends `{text, source_language}` to the MT service.
  - Every error is returned as `{"error": {"stage", "title", "message"}}`.
  - On startup, the console polls ASR/MT readiness and logs the result to its
    terminal/log without blocking startup.
- `static/index.html` is plain HTML and vanilla JS with no build step and no
  external fonts or scripts.
- `start_demo.py` is the launcher described above.

Tests (no models or microphone needed):

```bash
cd services/ai && ./.venv/Scripts/python.exe -m pytest demo_console/tests
```

## Extending it (Week 7 TTS, later moderation)

Future stages should extend this console, not replace it. The pipeline is a
plain ordered list on both sides:

- backend: `PIPELINE_STAGES` in `app.py`. Add a stage function and one
  `PipelineStage(...)` entry, and `POST /pipeline/<id>` is registered automatically.
- frontend: the `STAGES` array in `static/index.html`. Add one entry with the
  same shape and a matching panel.

A TTS playback panel (Week 7) and a moderation badge (later) each fit this pattern.

## Known limitation (found while building this, 2026-09-24)

The real ASR service (faster-whisper `small`, int8, CPU) was run on real
Telugu speech here for the first time, using 7s slices of the bake-off
recordings in `bakeoff/samples/`. It took **84–97s** of inference per 7s clip
and returned an empty string or garbled mixed-script text. The console reports
this correctly (as "took too long" or "No speech recognised"), but it is not
demo-quality on Telugu until the ASR model or config changes in
`services/ai/speech/`. That is out of scope for this directory. MT on correct
Telugu text works (for example, "నేను ఈ రోజు మా అమ్మతో మార్కెట్‌కి వెళ్తున్నాను."
-> "I am going to the market today with my mother." in about 1.4s).
