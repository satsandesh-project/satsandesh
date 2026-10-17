# SatSandesh AI — Speech, IndicConformer (`services/ai/speech_indicconformer/`)

A drop-in **alternative** ASR service: same `contracts/ai/transcribe.py` contract and
`POST /v1/transcribe` endpoint as `services/ai/speech/` (faster-whisper), backed by
AI4Bharat's IndicConformer instead. Background and measurements:
`docs/ASR_INDICCONFORMER_SPIKE.md`.

> **faster-whisper (`services/ai/speech/`) remains the fallback.** This service is not a
> replacement yet: its transcripts have not been checked by a Telugu/Hindi speaker, and
> CTC-vs-RNNT accuracy is undecided (see below).

## Model, license, gating

- `ai4bharat/indic-conformer-600m-multilingual` — one 600M-parameter multilingual model
  (22 Indian languages), run as ONNX via `transformers` + `onnxruntime`. No NeMo.
- MIT-licensed but **gated** on HuggingFace: accept the terms on the model page once with the
  account whose token you use.
- **`HF_TOKEN` is required.** Startup fails fast with a clear error if it is unset. Use a
  service credential rather than a personal token outside dev.

## Run it

```bash
cd services/ai
pip install -e ".[dev,indicconformer]"     # pinned to the versions the spike verified
export HF_TOKEN=hf_...
cd ../..
PYTHONPATH=. uvicorn services.ai.speech_indicconformer.app:app --port 8004
```

Ports: 8001 mock, 8002 faster-whisper ASR, 8003 moderation, **8004 this service**.
The model is loaded once at startup (~7–14 s on CPU) and a warm-up inference runs before
`/health/ready` turns 200. `/health/live` is 200 as soon as the process is up.

Settings (env): `HF_TOKEN` (required), `DECODE_MODE` (`ctc`|`rnnt`, default `ctc`),
`ASR_MODEL_NAME`, `ASR_PORT` (default 8004). Invalid values raise `SettingsError`.

## Differences from the faster-whisper service (read before swapping)

- **`language_hint` is required and must be `hi` or `te`.** The model takes an explicit
  language and cannot auto-detect; it has no English. A missing or `en` hint returns a
  `PipelineError` (`UNSUPPORTED_LANGUAGE`, HTTP 422).
- `detected_language` in the response is the hint echoed back, not a detection.

## DECODE_MODE: CTC vs RNNT

The model has two decoders. `DECODE_MODE` selects one for the service; it is configurable
rather than fixed because **the accuracy tradeoff is undecided**. The spike measured CTC as
faster (2.1 s vs 3.1 s on a 14.5 s clip) and the two produce slightly different words on some
clips, but no ground truth exists to say which is more accurate. `ctc` is the default for
speed only. Flip to `rnnt` to compare.

## Audio formats

Same as faster-whisper: `wav_pcm16` (mono, 16 kHz, 16-bit; stdlib `wave`), and `ogg_opus` /
`mp3` via the `ffmpeg` binary (`FFMPEG_PATH` or `PATH`). The decode helpers are imported from
`services/ai/speech/engine.py`, not copied. Consequence: importing them also imports
`faster_whisper`, so this service is coupled to the fallback's module; if `speech/` is ever
removed, move the helpers to a shared module. `torchaudio.load()`/torchcodec are **not**
used (broken against this machine's FFmpeg build).

## Tests

```bash
python -m pytest services/ai/speech_indicconformer/ -v
```

- `test_indicconformer_unit.py` — stubbed engine: settings validation, contract shape,
  format handling, error paths. No model, no token.
- `test_indicconformer_real.py` — loads the real model and transcribes
  `bakeoff/samples/mobile_01.wav` (gitignored sample); asserts Telugu-script output, not exact
  text. Skips with a clear message unless `HF_TOKEN` is set. Takes ~100 s.

## No-speech guard and optional denoise

Order: **denoise → no-speech guard → ASR**.

- **No-speech guard** (on by default; `ASR_NO_SPEECH_GUARD=off` disables). In CTC mode the
  model emits stray text for silence and white noise (evidence in `services/ai/DECISIONS.md`
  #13), so input that is nearly silent (RMS < 0.003) or noise-like (spectral flatness > 0.5)
  returns `text=""` with `detected_language` = the language hint, without running the model.
  An unsupported/missing language hint is still a 422. Thresholds: `ASR_NO_SPEECH_MIN_RMS`
  (0.003) and `ASR_NO_SPEECH_MAX_FLATNESS` (0.5); every gated clip is logged at WARNING with its
  measured RMS and flatness.
- **Denoise** (`ASR_DENOISE=off|rnnoise`, default `off`): RNNoise via the optional
  `denoise` extra (`pip install ".[denoise]"`), shared with the faster-whisper service
  (`speech/denoise.py`). Adds a `denoise` stage timing when on. Compare a recording with
  `python services/ai/tools/denoise_ab.py sample.wav --url http://localhost:8004 --language te`.
