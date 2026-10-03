# SatSandesh AI — MT Pivot (`services/ai/mt/`)

The real machine-translation pivot service — not the mock. Loads
[`ai4bharat/indictrans2-indic-en-dist-200M`](https://huggingface.co/ai4bharat/indictrans2-indic-en-dist-200M)
once at startup and serves `POST /v1/pivot` against `contracts/ai/pivot.py`.

**This is the source-language → English direction only.** It translates a
circle message's original text (Hindi or Telugu) into an English "pivot" —
the intermediate form used for moderation and before per-receiver rendering.
The reverse direction (English → target language, e.g. English → Hindi or
English → Telugu) is a **separate** service, `contracts/ai/render.py`, built
alongside TTS in **Week 7** — do not confuse the two.

## Run it

```bash
cd services/ai
./.venv/Scripts/python.exe -m pip install -e ".[mt]"   # once, to pick up indictranstoolkit + transformers + torch
PYTHONPATH=../.. ./.venv/Scripts/python.exe -m uvicorn mt.app:app --port 8004
```

Port **8004** — 8001 is the mock server, 8002 is the real ASR service
(`services/ai/speech/`, `feat/ai-speech-asr-w5`, not yet merged to main as of
this branch), 8003 is already claimed by `services/ai/moderation/` (`MOD_PORT`
default, merged to main). 8004 is the next free port.

Startup loads the model and runs one warm-up translation before the process
reports ready — `GET /health/ready` returns `503` until both are done, `200`
after. `GET /health/live` returns `200` immediately regardless of model state.

### Hugging Face access

`ai4bharat/indictrans2-indic-en-dist-200M` is a **gated** repo on Hugging Face
— MIT-licensed and publicly downloadable, but only after logging in and
clicking "Agree" on the model page. The process needs a Hugging Face token
with access to that repo, either cached locally (`huggingface-cli login`) or
via the `HF_TOKEN` environment variable, before `engine.load()` can succeed.

## Configuration

Read once at import time by `services/ai/mt/settings.py`. A present-but-invalid
value fails fast and loudly at startup; a missing value falls back to the
documented default below — there is no silent, undocumented fallback.

| Env var | Default | Notes |
|---|---|---|
| `MT_MODEL_NAME` | `ai4bharat/indictrans2-indic-en-dist-200M` | the distilled indic-en model — NOT en-indic (that's Week 7) |
| `MT_DEVICE` | `auto` | `auto` resolves to `cuda` if available else `cpu`; this dev machine has no CUDA GPU |
| `MT_NUM_BEAMS` | `5` | beam search width, matches AI4Bharat's documented example |
| `MT_MAX_LENGTH` | `256` | token truncation/generation cap, matches AI4Bharat's documented example |
| `MT_PORT` | `8004` | |

## Language codes: BCP-47 on the wire, FLORES-200 to the model

`contracts/ai/language.py`'s `LanguageCode` is BCP-47 (`en`/`hi`/`te`) —
deliberately model-agnostic (see that file's docstring). IndicProcessor needs
FLORES-200-style codes instead (`hin_Deva`, `tel_Telu`, `eng_Latn`). The
mapping lives in `services/ai/mt/engine.py` (`flores_code_for`), not in the
contract — model vocabulary is a service-layer implementation detail.

## Open question: English-source input (assumption, not confirmed contract behavior)

`contracts/ai/pivot.py`'s `PivotRequest`/`PivotResponse` say nothing about
what should happen when `source_language` is already `en` — no field, no
docstring. This service **assumes passthrough**: `pivot_text` echoes
`request.text` unchanged, `model_version` is reported as
`"passthrough (no MT model invoked; source_language == en)"`, and the MT model
is never invoked. This is a reasonable default, not a team-confirmed contract
behavior — flagging for the contract owner to confirm or override.

The same passthrough path handles empty/whitespace-only `text` (a realistic
case when ASR produced nothing useful from a silent or noisy recording):
`pivot_text` is `""`, no error, no model call.

## `PivotResponse` has no `stage_timings` field

Unlike `TranscribeResponse`, `contracts/ai/pivot.py`'s `PivotResponse` has
only a single `duration_ms: float`, no `stage_timings: list[StageTiming]` —
confirmed by reading the contract fresh, not assumed from the ASR service's
shape. This service still measures real preprocess/inference/postprocess
durations internally (`engine.py`'s `TranslationResult`) and logs that
breakdown (`services.ai.mt.app` logger, `INFO` level) for observability, but
only the summed total goes out over the wire in `duration_ms` — there is no
contract field to carry the per-stage split. If per-stage timing on the wire
turns out to matter for MT specifically, that's a contract change for the
team to make, not something this service can add unilaterally.

## Sentence handling

This phase handles single-sentence or short-paragraph input directly, the
same way `IndicProcessor.preprocess_batch` is documented to — no
sentence-segmentation pipeline was built. Multi-sentence voice-note-length
input was not observed to degrade translation quality badly enough in testing
to justify one; if that changes with real bake-off audio, that's tuning work
for a later phase, not a gap in this one.

## Development

```bash
cd services/ai
PYTHONPATH=../.. ./.venv/Scripts/python.exe -m pytest mt/ -v
```

Tests load the real model (not mocked) against short, known Telugu and Hindi
sentences — they exercise the real preprocess → generate → postprocess
pipeline mechanically and confirm the output is genuinely translated (not an
echo of the input), not real-speech transcription-to-translation accuracy.
That end-to-end accuracy check against real Telugu audio is the bake-off, a
separate manual step, not this phase's job.
