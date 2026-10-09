# Demo console (`services/ai/demo_console/`)

A small local web page that runs the **real** AI pipeline end to end, for live
demos and manual testing:

```
Part 1   browser mic -> WAV -> console -> ASR (IndicConformer, :8002) -> MT (IndicTrans2 indic->en, :8004) -> English text
Part 2   English text -> render (IndicTrans2 en->indic + Piper TTS, :8006) -> Telugu/Hindi text + spoken audio
```

**This is a demo/testing harness, not the product UI.** The real user-facing app
is `clients/elder-app/` (owned by M1). The console talks to the ASR, MT and
render services only over their public HTTP APIs (`POST /v1/transcribe`,
`POST /v1/pivot`, `POST /v1/render`). It never imports their engines.

## Run it (the one command)

From the repo root, in a PowerShell window where `HF_TOKEN` is set:

```powershell
$env:HF_TOKEN = "hf_..."          # your read token; never printed or stored by the demo
services\ai\.venv\Scripts\python.exe services\ai\demo_console\start_demo.py
```

It starts ASR, MT, render and the console (in that order), waits until all four
report ready, prints `DEMO READY: http://127.0.0.1:8005/` and opens the browser.
Leave the window open. Ctrl+C stops only the processes this command started.

- `--asr whisper` runs the faster-whisper service instead of IndicConformer
  (fallback only; when last measured on 2026-09-24, before its VAD change, it took 84-97 s per 7 s Telugu clip and returned empty or garbled text. Not re-measured since). `--no-browser` prints the URL without opening it.
- A service already running on its port is **reused**, not restarted, and the
  model version it reports is printed. If the ASR on :8002 is not the one you
  asked for, a loud `!! WARNING` line says so. Nothing is killed.
- If one service dies mid-demo, run the same command again; only the missing one starts.
- The wait for readiness is finite (`DEMO_READY_TIMEOUT_S`, default 900 s) and
  names the service that is not ready. Progress is printed every 15 s.
- Logs go to `demo_console/_logs/` (gitignored).

## Ports

| Port | Service | Why this port |
|---|---|---|
| 8002 | ASR (IndicConformer, or faster-whisper with `--asr whisper`) | Launcher sets `ASR_PORT=8002`. IndicConformer's own default is 8004. |
| 8004 | MT (indic -> English) | MT's default. |
| 8005 | Demo console | `DEMO_CONSOLE_PORT`. |
| 8006 | Render (English -> text + audio) | Launcher sets `RENDER_PORT=8006`. Render's own default is 8005, which is the console's. The console reads `DEMO_RENDER_PORT` (default 8006) and never imports render's settings. |

## What must be on this machine (children run offline)

Children run with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, so models load
from the local Hugging Face cache only.

- HF cache entries: `models--ai4bharat--indic-conformer-600m-multilingual`,
  `models--ai4bharat--indictrans2-indic-en-dist-200M`,
  `models--ai4bharat--indictrans2-en-indic-dist-200M`
  (`models--Systran--faster-whisper-small` only for `--asr whisper`).
- Piper voices `te_IN-maya-medium` and `hi_IN-rohan-medium` (`.onnx` + `.onnx.json`)
  in `services/ai/render/voices/` (or the folder in `RENDER_VOICE_DIR`).
- `piper-tts` installed in `services/ai/.venv`.
- **`HF_TOKEN` in the environment.** IndicConformer and render both refuse to
  start without it, even though the models load from cache. The launcher warns
  before starting if it is missing. The token is passed to the children and is
  never printed or logged.
- The en->indic translation model (render) and IndicConformer are gated on Hugging Face: accept the terms once, online, with the account the token belongs to.

**Offline status: not yet verified.** Render's `TtsEngine._fetch()` calls
`hf_hub_download(..., local_dir=voices)` for both voices on every start. Whether
that call succeeds with `HF_HUB_OFFLINE=1` when the files are already on disk has
**not** been tested on this machine (render could not be started: no token, no
`piper-tts`). Until it is, do not promise "no network needed". If render fails
to start offline, the smallest launcher-only change is to run the render child
without `HF_HUB_OFFLINE` (keeping `TRANSFORMERS_OFFLINE=1`), which means render
contacts huggingface.co for the voice files at start-up.

## What the page shows

**Part 1 - Speak -> English**
1. Language toggle: Telugu (default) / Hindi.
2. **Start recording / Stop recording**, a live elapsed counter, and a live
   input-level meter (a dead microphone is visible within a second or two).
   Hard cap `DEMO_MAX_RECORD_SECONDS` (default 20 s); it stops by itself.
3. Step status (Upload -> Transcribe -> Translate) with elapsed seconds.
4. Panels: transcript, English translation, latency per stage. The ASR/MT/render
   figures come from the services' own responses plus the console-measured round
   trip. The upload figure is measured in the browser. Nothing is estimated.
5. A typed-text box that goes straight to MT (fallback when the mic or ASR is unavailable).
6. "Advanced: record on the console PC's own microphone" - the old server-side
   7 s recording, kept but no longer the default.

**Part 2 - Family reply -> spoken**
1. English box, pre-filled with the Part 1 translation when there is one;
   editable, and usable on its own.
2. Target language (defaults to the Part 1 language), **Translate and speak**.
3. Shows the translated text, an audio player (autoplay tried once; the play
   button is the fallback), a **Replay** button, the audio length, and latency.
   The last audio stays on screen until the next successful run.
4. If render reports a degraded result (text only / TTS skipped), the text is
   shown with "Audio is not available for this one". That is not treated as a failure.
5. Render reports one duration for translate + speak together; the console shows that
   one number plus the round trip and does not invent a split.

The status line shows each service's state and the model version it reports.
Every failure shows a short message and **Try again**; no stack traces.

## Browser microphone notes

- Open the page as **`http://127.0.0.1:8005/`**, not a LAN IP or computer name.
  Browsers only allow the microphone on secure origins and `127.0.0.1` counts as one.
  The page shows a warning if it was opened another way.
- Click **Allow** when the browser asks. If it was blocked, use the icon in the
  address bar to allow it, then Try again. With no microphone the page says so and
  typing still works.
- The page records raw samples, downsamples to 16 kHz mono and builds a 16-bit WAV
  in JavaScript. It does **not** use `MediaRecorder` (webm/opus is not in the
  contract's `AudioFormat`). See `services/ai/DECISIONS.md` #14.

## How it's built

- `app.py` (FastAPI, port 8005). Endpoints:
  - `GET /health`, `GET /health/deps` (ASR/MT/render state and model versions).
  - `POST /pipeline/upload` (browser WAV, `audio/wav`): checks RIFF header, 16 kHz,
    mono, 16-bit, non-empty, size cap, duration cap and the silence threshold, then
    writes `_last_recording.wav`. Returns `{duration_s, peak}`.
  - `POST /pipeline/record` (server mic), `/pipeline/transcribe`, `/pipeline/translate`.
  - `POST /pipeline/render` `{text, target_language}`.
  - `GET /pipeline/audio/{name}`: serves only `^render-[0-9a-f]{32}-(te|hi)\.wav$`
    that resolves inside the render output directory (`RENDER_OUTPUT_DIR`, default
    `services/ai/render/output/`). Render and the console must use the same directory;
    the launcher passes the environment through to both.
  - Every error is `{"error": {"stage", "title", "message"}}`.
- `static/index.html`: plain HTML and vanilla JS, no build step, no external fonts or scripts.
- `start_demo.py`: the launcher. `build_services()` and `child_env()` are unit-tested.

Tests (no models, microphone or network needed):

```bash
cd services/ai && ./.venv/Scripts/python.exe -m pytest demo_console/tests
```

## Presenting

1. **Start it 10 minutes early.** Cold-start time on the demo machine has **not
   been measured yet** (see the PR). Run it once the day before and note the number.
2. Wait for `DEMO READY`, then open `http://127.0.0.1:8005/` and check all three
   status dots are green. Do one practice sentence in Telugu and one in Hindi.
3. Say: "I speak in Telugu; the system writes it down, turns it into English, and
   then a family reply in English comes back spoken in Telugu."
4. If something fails:
   - **Mic blocked / no mic:** allow it in the address bar, or use the typed-text box.
   - **Recording silent:** check the meter moves while you speak; check the mic is not muted.
   - **ASR not running / too slow:** run the start command again (it restarts only the missing
     service); fall back to the typed-text box; as a last resort restart with `--asr whisper`.
   - **MT not running:** run the start command again; Part 1 stops after the transcript.
   - **Render not running or no audio:** run the start command again; if the page says
     "audio is not available", the translated text is still shown.
5. Transcript and audio correctness have **not** been checked by a Telugu/Hindi speaker.
   Do that before presenting.

## Extending it (later: moderation badge)

Future stages extend this console, not replace it. The pipeline is a plain ordered list on both sides:

- backend: `PIPELINE_STAGES` in `app.py`. Add a stage function and one
  `PipelineStage(...)` entry, and `POST /pipeline/<id>` is registered automatically.
- frontend: `STAGE_DEFS` in `static/index.html`. Add one entry and a panel.

A moderation badge fits this pattern.
