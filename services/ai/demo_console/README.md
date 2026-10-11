# Demo console (`services/ai/demo_console/`)

A small local web page that runs the **real** AI pipeline end to end, for live
demos and manual testing:

```
Telugu/Hindi note   browser mic -> WAV -> console -> ASR (IndicConformer, :8002) -> MT (IndicTrans2 indic->en, :8004) -> English text
English note        browser mic -> WAV -> console -> ASR (faster-whisper, :8007) -> English text   (no MT: it already is the pivot)
Reply (te/hi)       English text -> render (IndicTrans2 en->indic + Piper TTS, :8006) -> Telugu/Hindi text + spoken audio
Reply (en)          English text -> the console's own Windows voice (demo only, not render) -> spoken audio
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

It starts IndicConformer ASR, faster-whisper (English), MT, render and the console, waits
until all five report ready, prints `DEMO READY: http://127.0.0.1:8005/` and opens the browser.
Leave the window open. Ctrl+C stops only the processes this command started.

- `--asr-en whisper|off` (default `whisper`): `off` does not start the English ASR. Typed English
  still works; an English *recording* gets a calm "English speech needs the faster-whisper
  service" message with Try again; Telugu and Hindi are unaffected.
- `--asr whisper` runs faster-whisper as the *main* ASR on :8002 instead of IndicConformer
  (fallback only; measured 2026-09-24, before its VAD change: 84-97 s per 7 s Telugu clip and
  empty or garbled text; not re-measured). In that mode the same service also hears English, so no
  second whisper is started.
- `--en-voice windows|off` (default `windows`): who speaks an *English* reply. `windows` uses the
  built-in Windows voice (see "English replies" below); `off` makes English replies text only.
- `--no-browser` prints the URL without opening it.
- A service already running on its port is **reused**, not restarted, and the model version it
  reports is printed. If the service on :8002 is not the ASR you asked for, or the one on :8007 is
  not faster-whisper, a loud `!! WARNING` line says so. Nothing is killed.
- If one service dies mid-demo, run the same command again; only the missing one starts.
- The wait for readiness is finite (`DEMO_READY_TIMEOUT_S`, default 900 s) and names the service
  that is not ready. Progress is printed every 15 s.
- Logs go to `demo_console/_logs/` (gitignored).

## Ports

| Port | Service | Why this port |
|---|---|---|
| 8001 | mock server | not started by the demo |
| 8002 | ASR for Telugu/Hindi (IndicConformer; faster-whisper with `--asr whisper`) | Launcher sets `ASR_PORT=8002`. IndicConformer's own default is 8004. |
| 8003 | moderation | not started by the demo |
| 8004 | MT (indic -> English) | MT's default. |
| 8005 | Demo console | `DEMO_CONSOLE_PORT`. |
| 8006 | Render (English -> text + audio) | Launcher sets `RENDER_PORT=8006`. Render's own default is 8005, which is the console's. The console reads `DEMO_RENDER_PORT` (default 8006). |
| 8007 | ASR for English (faster-whisper, `speech.app`) | Next free port. `speech.app` reads `ASR_PORT`, so the launcher sets `ASR_PORT=8007` for that child only. The console learns the port from `DEMO_ASR_EN_PORT`. |

## What must be on this machine (children run offline)

Children run with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, so models load
from the local Hugging Face cache only.

- HF cache entries: `models--ai4bharat--indic-conformer-600m-multilingual`,
  `models--ai4bharat--indictrans2-indic-en-dist-200M`,
  `models--ai4bharat--indictrans2-en-indic-dist-200M`
  `models--Systran--faster-whisper-small` (English speech; also the `--asr whisper` fallback).
- Piper voices `te_IN-maya-medium` and `hi_IN-rohan-medium` (`.onnx` + `.onnx.json`)
  in `services/ai/render/voices/` (or the folder in `RENDER_VOICE_DIR`).
- `piper-tts` installed in `services/ai/.venv`.
- **`HF_TOKEN` in the environment.** IndicConformer and render both refuse to
  start without it, even though the models load from cache. The launcher warns
  before starting if it is missing. The token is passed to the children and is
  never printed or logged.
- The en->indic translation model (render) and IndicConformer are gated on Hugging Face: accept the terms once, online, with the account the token belongs to.

**Offline status:** all five services started with `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` on this machine (render included, with both Piper voices already on disk),
and the page itself requests nothing outside `127.0.0.1` (no CDN, web fonts, remote scripts or
images; checked in a headless browser). Not verified: a first start on a machine whose cache is
empty (it would need the network once).

## Language behaviour

The **speaker language** (English / Telugu / Hindi, next to the microphone) is what the person
speaks. It decides which ASR hears the note and is sent to it as the language hint. It starts as the
page language and is **independent of it afterwards**.

| Speaker language | ASR | MT | Shown as the English text |
|---|---|---|---|
| Telugu | IndicConformer :8002 (hint `te`) | called (indic -> English) | MT output (behind "Show English") |
| Hindi | IndicConformer :8002 (hint `hi`) | called | MT output |
| English | faster-whisper :8007 (hint `en`) | **skipped**, the transcript is the pivot | the transcript itself, with the producing model named |

Replying: the reply is written in English and spoken in English, Telugu or Hindi. Telugu and Hindi
go through render (text + audio).

### English replies (demo-only voice)

Render has no English voice (and `contracts/ai/render.py` requires audio in every result), so the
console never calls render for an English reply. It speaks it itself with `english_tts.py`:
Windows' built-in speech engine (`System.Speech`, run in a PowerShell subprocess), writing a 16 kHz
mono 16-bit WAV into `demo_console/_audio/` (gitignored; the newest 20 files are kept).

- **Demo only, Windows only, not a product decision.** The product needs a licensed Piper English
  voice served by render; that is a separate task.
- The reply text never goes into the command line. The PowerShell script is a fixed string; the text
  and output path travel in environment variables (`SAT_TTS_TEXT`, `SAT_TTS_OUT`), and `HF_TOKEN` is
  not passed on. Hard timeout 30 s; replies over 1000 characters are refused with a calm message.
- The exact voice used is reported as `windows-sapi:<voice name>` and shown under **Details**
  (on the dev laptop it was `Microsoft David Desktop`).
- If it cannot run (not Windows, no English voice installed, the engine fails or times out, or
  `--en-voice off`) the reply is shown as text with a short note. It is never an error page.
- `GET /pipeline/audio/{name}` serves `en-<32 hex>.wav` only from `_audio/`, and render's
  `render-<32 hex>-te|hi.wav` only from render's output directory; neither pattern is accepted in the
  other directory.

## Page language

The switcher at the top (English / తెలుగు / हिन्दी) re-words the whole page instantly, without a
reload. Notes, replies, audio, text boxes and the speaker language are kept. Strings are in
`static/i18n/{en,te,hi}.json`; the choice is remembered in `localStorage` (inside try/catch, the page
works without it). `<html lang>` follows the page language and every transcript or reply block gets
its own `lang=` so screen readers pronounce it correctly. Fonts are a system stack
(`Nirmala UI` first); nothing is fetched.

**None of the Telugu or Hindi wording has been checked by a native speaker.** Every te/hi string
is listed in `static/i18n/NEEDS_NATIVE_REVIEW.md` with its English source and where it came from
(the elder app has English and Telugu only, so all Hindi is new). A test fails if a string is not
listed. Error text that comes from a service and has no key is shown in English.

## What the page shows

1. **Speak a note**: speaker language, Start/Stop recording with a live level meter (a dead
   microphone is visible within a second or two; hard cap `DEMO_MAX_RECORD_SECONDS`, default 20 s),
   step pills, and a typed-text box that skips ASR. "Advanced" keeps the old server-side 7 s recording.
2. **The conversation**: the note as a bubble (original text, plus **Show English** like the app),
   then a reply box (English, editable, pre-filled with the note's English) and the reply bubble with
   the text, a **Play / Play again** button and the audio length. A degraded render result is shown
   as text with "Audio is not available for this one", not as an error.
3. **Details** (collapsed): service versions, the models that produced each result, and the measured
   time per step. The ASR/MT/render figures come from the services' own responses plus the
   console-measured round trip; the upload figure is measured in the browser. Nothing is estimated.
   Render reports one duration for translate + speak, so the console shows that one number.

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
  - `GET /health`, `GET /health/deps` (ASR, English ASR, MT, render: state and model versions).
  - `POST /pipeline/upload` (browser WAV, `audio/wav`): checks RIFF header, 16 kHz,
    mono, 16-bit, non-empty, size cap, duration cap and the silence threshold, then
    writes `_last_recording.wav`. Returns `{duration_s, peak}`.
  - `POST /pipeline/record` (server mic), `/pipeline/transcribe` (`en`/`te`/`hi`, routed by
    `asr_upstream_for`), `/pipeline/translate` (`en` returns the text unchanged with
    `skipped: true` and calls nothing).
  - `POST /pipeline/render` `{text, target_language}` (`en`: text only, render is not called).
  - `GET /pipeline/audio/{name}`: serves only `^render-[0-9a-f]{32}-(te|hi)\.wav$`
    that resolves inside the render output directory (`RENDER_OUTPUT_DIR`, default
    `services/ai/render/output/`). Render and the console must use the same directory;
    the launcher passes the environment through to both.
  - Every error is `{"error": {"stage", "title", "message"}}`, plus `code` / `service` when the
    page can word it itself.
- `static/index.html` + `static/i18n/*.json`: plain HTML and vanilla JS, no build step, no external
  fonts or scripts.
- `start_demo.py`: the launcher. `build_services()` and `child_env()` are unit-tested.

Tests (no models, microphone or network needed):

```bash
cd services/ai && ./.venv/Scripts/python.exe -m pytest demo_console/tests
```

## Memory and start-up time (measured)

Measured 2026-10-11 on the dev laptop (15.7 GB RAM, 12 logical CPUs, no GPU), all five services
started together with the models already in the OS file cache:

- **Start to `DEMO READY`: 39 s** (English ASR 22 s, IndicConformer 30 s, console 35 s, MT 36 s,
  render 39 s). A first start after a reboot, with a cold disk cache, was not measured and will be slower.
- **Idle memory** (Windows working set / private bytes): IndicConformer 2.8 / 2.9 GB, render 1.3 / 2.8 GB,
  MT 0.9 / 2.3 GB, faster-whisper 0.4 / 2.7 GB, console 0.07 / 0.4 GB. Working sets sum to **5.5 GB**;
  free RAM fell from 6.2 GB to 1.6 GB while the demo was up. Close other heavy apps first.

## Presenting

1. **Start it 10 minutes early** and wait for `DEMO READY` (about 40 s warm). Open
   `http://127.0.0.1:8005/` and check the four status dots are green.
2. Do one practice sentence in each language you will show. Choose the page language first; it
   does not change what the microphone expects, the speaker language does.
3. Say: "I speak in Telugu; the system writes it down, turns it into English, and a family reply in
   English comes back spoken in Telugu." For English: "English needs no translation step."
4. If something fails:
   - **Mic blocked / no mic:** allow it in the address bar, or use the typed-text box.
   - **Recording silent:** check the meter moves while you speak; check the mic is not muted.
   - **"English speech needs the faster-whisper service":** run the start command again (without
     `--asr-en off`); meanwhile type the English sentence. Telugu and Hindi keep working.
   - **ASR / MT not running or too slow:** run the start command again (it starts only what is missing).
   - **Render not running or no audio:** run the start command again; the translated text still shows.
5. **Not checked by a person:** Telugu and Hindi transcripts, Telugu and Hindi page wording, and how
   understandable the spoken replies are. Have a native speaker check these before presenting. The
   English reply voice is the Windows demo voice, whose quality was not listened to by a person; on a
   machine without an English Windows voice, English replies are text only.

## Extending it (later: moderation badge)

Future stages extend this console, not replace it. The pipeline is a plain ordered list on both sides:

- backend: `PIPELINE_STAGES` in `app.py`. Add a stage function and one
  `PipelineStage(...)` entry, and `POST /pipeline/<id>` is registered automatically.
- frontend: `STAGE_DEFS` in `static/index.html`. Add one entry and a panel.

A moderation badge fits this pattern.
