# services/ai/render/: English pivot text → target-language text + audio

Real implementation of the render step in `contracts/ai/render.py`. Sibling to
`services/ai/mt/` (which does the reverse, source → English, direction) and
built the same way (`settings.py`, `engine.py`, `app.py`, `tests/`).

`POST /v1/render` takes `{pivot_text, target_languages}` and, for each requested
language, does two internal steps and merges them into one `RenderResult`:

1. **Translate** English → target with `ai4bharat/indictrans2-en-indic-dist-200M`
   (via IndicTransToolkit; `eng_Latn` → `hin_Deva` / `tel_Telu`).
2. **Synthesize** that text with a Piper voice: `te_IN-maya-medium` (Telugu) or
   `hi_IN-pratham-medium` (Hindi). Choice of voices comes from
   `docs/RENDER_ENVIRONMENT_SPIKE.md`.

Endpoints: `GET /health/live`, `GET /health/ready` (503 until the MT model is loaded
and warm and both voices are loaded and have each synthesized once), `POST /v1/render`.
Default port **8005** (8001 mock, 8002 ASR, 8003 moderation, 8004 MT).

## Setup

```
# from services/ai/: base deps + the "mt" extra (torch, transformers==4.44.2,
# indictranstoolkit, sentencepiece), then this service's extras
pip install -e ".[mt]"
pip install -r render/requirements.txt        # piper-tts, huggingface_hub
HF_TOKEN=hf_... PYTHONPATH=../.. python -m uvicorn services.ai.render.app:app --port 8005
```

### HF_TOKEN and gate acceptance: required, and it is a SEPARATE gate

`HF_TOKEN` is read from the environment only (never hardcoded or committed). Startup
fails immediately with `SettingsError` if it is missing.

**`ai4bharat/indictrans2-en-indic-dist-200M` is gated separately from
`indictrans2-indic-en-dist-200M`.** Accepting the indic-en terms (Week 6) does **not**
grant access to en-indic. Verified 2026-09-29: with a token that could download
`indic-en/config.json`, the same call for `en-indic` returned `GatedRepoError 403`.
To fix: log in to Hugging Face with the account the token belongs to, open
https://huggingface.co/ai4bharat/indictrans2-en-indic-dist-200M and click "Agree"
(auto-approved). Then restart.

The Piper voices come from `rhasspy/piper-voices`, which is **public and ungated**. They
are downloaded on first start into `render/voices/` and the HF token is deliberately not
sent there.

## Piper voice licence: UNRESOLVED

`hi_IN-pratham-medium`'s model card says its training data is licensed
**CC BY-NC-SA 4.0 (non-commercial, share-alike)**. This service currently uses it as the
Hindi voice because it is the one confirmed working in the spike. **That is an open
licensing question for the team, not a cleared choice.** If SatSandesh is or becomes
commercial, this voice may not be usable.

The alternatives `hi_IN-priyamvada-medium` and `hi_IN-rohan-medium` exist in the Piper
catalogue, but their model cards were **not read** in the spike, so their licences are
unknown. Nothing has been swapped. To try one, set `RENDER_VOICE_HI` (and read its card
first). The Telugu voice's card (`maya`) points to the IIT Madras "indictts" licence,
which was also not read.

## Configuration

| Env var | Default |
|---|---|
| `HF_TOKEN` | **required** |
| `RENDER_MT_MODEL_NAME` | `ai4bharat/indictrans2-en-indic-dist-200M` |
| `RENDER_MT_DEVICE` | `auto` (cuda if available, else cpu; this dev machine has no CUDA) |
| `RENDER_NUM_BEAMS` / `RENDER_MAX_LENGTH` | `5` / `256` |
| `RENDER_VOICE_TE` / `RENDER_VOICE_HI` | `te_IN-maya-medium` / `hi_IN-pratham-medium` |
| `RENDER_VOICE_DIR` | `render/voices/` (gitignored) |
| `RENDER_OUTPUT_DIR` | `render/output/` (gitignored) |
| `RENDER_PORT` | `8005` |

## Output audio

Each synthesized result is written as `render-<uuid>-<lang>.wav` (mono, 16-bit PCM,
22,050 Hz) under `RENDER_OUTPUT_DIR`, and `RenderResult.audio.uri` is the absolute
`file:///...` URI of that file. Nothing cleans this directory up yet. (The mock uses
`mock://audio/...` URIs; there is no blob storage yet. See the contract's Open Questions.)

## Contract fit and behaviours

- One combined result per language: `duration_ms` is translate + synthesize time. The
  contract has no per-stage timing field, so the split is only logged.
- `RenderResult.audio` is **required** (not Optional). A language with no audio gets
  `audio.uri == ""`, `duration_ms == 0`, plus `degraded.active = true`. Callers must
  check `degraded` before using `audio`.
- **`en` is a valid target language in the contract, but there is no English voice.** For
  `en`, `text` is the pivot text unchanged and the result is `degraded` with
  `TEXT_ONLY`. This is the case that exercises the per-language degraded design, and it
  is an assumption (not confirmed contract behaviour) that echo is right for `en`.
- If TTS raises for one language, that language returns its translated text with
  `TTS_SKIPPED`, and other languages in the fan-out are unaffected.
- Empty/whitespace `pivot_text` returns a `TEXT_ONLY`-degraded result with `text == ""`
  per language (same empty-passthrough choice as `services/ai/mt/`).
- The top-level `RenderResponse.degraded` is active whenever any language degraded.
- Empty `target_languages` is rejected by the contract's own validator (HTTP 422).
  Duplicates are removed by it.

## Timing (this dev machine, CPU only)

Measured with a stubbed MT engine and real Piper:

| Step | Time |
|---|---|
| Load Telugu voice (`te_IN-maya-medium`, includes download check) | 3.5 s |
| Load Hindi voice (`hi_IN-pratham-medium`) | 7.2 s |
| Warm-up synthesis, Telugu / Hindi | 0.35 s / 0.48 s |
| Live render, hi (2.7 s of audio), stub MT + Piper | ~0.99 s |
| Live render, te (3.9 s of audio), stub MT + Piper | ~1.37 s |

**MT load, warm-up and translation timing are NOT measured yet.** The en-indic model
could not be downloaded with the available token (see the gate section above), so that
half has only been tested against a stub. It has never been run against the real model.

## Tests

```
# from services/ai/ (the default `pytest` testpaths do not include render/tests)
PYTHONPATH=../.. python -m pytest render/tests -v
```

Piper is always real. MT-dependent logic runs against a stub; one test runs the real
en-indic model and skips itself when the gate has not been accepted. Run the whole
suite with `HF_TOKEN` set.
