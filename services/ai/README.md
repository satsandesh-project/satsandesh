# SatSandesh AI Services (`services/ai/`)

Owned by Student 3 (AI/GPU). This is the consumer-facing reference for the
`contracts/ai/` Pydantic models and the mock server other members build against.
You should not need to read the implementation to use this.

Contract shape version: `CONTRACTS_VERSION = "0.1.0"` (`contracts/ai/common.py`).
Every request/response carries `contract_version` so you can tell which shape you
are looking at as this evolves week to week.

## Quickstart: running the mock server

```bash
cd services/ai
py -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .[dev]
./.venv/Scripts/python.exe -m uvicorn mock.app:app --reload --port 8001
```

Interactive docs at `http://localhost:8001/docs` once it's running. Every response
is a real instance of the Pydantic response model below — nothing is hand-typed
JSON, so if it validates against the contract in tests, it validates for you too.

Artificial latency: every endpoint sleeps before responding, so you can develop
against realistic timing without a GPU.

- Default: `MOCK_LATENCY_MS` env var (default `50`ms).
- Per-request override: `X-Mock-Latency-Ms` header, e.g. `-H "X-Mock-Latency-Ms: 800"`
  to simulate a slow ASR call.

## Message envelope (not my contract — read this first)

The gateway (not this package) defines the real envelope every message travels
in: `{ "type", "id", "ts", "payload" }`. `contracts/ai/envelope.py` documents a
*proposed* shape so this package's own tests can round-trip realistically, but no
request/response model here imports or depends on it. When you call the real AI
services (or the mock server) in Week 2+, you nest one of the payloads below
inside whatever envelope the gateway actually ships. Don't code against
`contracts/ai/envelope.py` as if it's final — see Open Questions.

## Language codes

`contracts.ai.language.LanguageCode` — closed enum, BCP-47 primary subtags only:

| Value | Language |
|-------|----------|
| `en`  | English  |
| `hi`  | Hindi    |
| `te`  | Telugu   |

Tamil (`ta`) and Kannada (`kn`) are **not** in the enum yet — v1 stretch goals,
not to be assumed anywhere. A bare string is never accepted; only these three
values validate. See `DECISIONS.md` for why BCP-47 was chosen despite no model in
the pipeline speaking it natively.

## Degraded mode

Every response carries a `degraded` field:

```json
{ "active": false, "reason": "none", "detail": null }
```

`reason` is one of `none`, `text_only`, `tts_skipped`, `model_fallback`,
`rate_limited`. If `active` is `true`, treat the rest of the response as a
best-effort partial result, not a complete one — check `reason` for what to tell
the user (e.g. `tts_skipped` means show text, don't expect audio to arrive).

## Endpoints

### `POST /v1/transcribe` — audio → source-language text

Request (`TranscribeRequest`):
```json
{
  "contract_version": "0.1.0",
  "audio": {
    "uri": "file:///tmp/sample.wav",
    "format": "wav_pcm16",
    "duration_ms": 30000,
    "sample_rate_hz": 16000
  },
  "language_hint": "te"
}
```
`language_hint` is optional — omit it to let ASR auto-detect.

Response (`TranscribeResponse`):
```json
{
  "contract_version": "0.1.0",
  "text": "నమస్తే, ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
  "detected_language": "te",
  "model_version": "faster-whisper-small-int8@1",
  "duration_ms": 812.4,
  "stage_timings": [
    { "stage": "preprocess", "duration_ms": 40.1 },
    { "stage": "inference", "duration_ms": 700.3 },
    { "stage": "postprocess", "duration_ms": 72.0 }
  ],
  "degraded": { "active": false, "reason": "none", "detail": null }
}
```

### `POST /v1/pivot` — any supported language → English pivot text

Request (`PivotRequest`):
```json
{ "contract_version": "0.1.0", "text": "నమస్తే...", "source_language": "te" }
```
`source_language` may be `en` (identity pass) — some content may already be
English.

Response (`PivotResponse`):
```json
{
  "contract_version": "0.1.0",
  "pivot_text": "Namaste, what time is today's satsang?",
  "source_language": "te",
  "model_version": "indictrans2-distilled@1",
  "duration_ms": 95.2,
  "degraded": { "active": false, "reason": "none", "detail": null }
}
```

### `POST /v1/render` — English pivot → per-language text + audio, fanned out

One request can target multiple languages at once, because a circle message
fans out to many receivers in one shot.

Request (`RenderRequest`):
```json
{
  "contract_version": "0.1.0",
  "pivot_text": "Namaste, what time is today's satsang?",
  "target_languages": ["hi", "te"]
}
```
`target_languages` must be non-empty. Duplicates are silently deduplicated,
preserving first-occurrence order — you will never get two results for the same
language back.

Response (`RenderResponse`):
```json
{
  "contract_version": "0.1.0",
  "results": [
    {
      "language": "hi",
      "text": "नमस्ते, आज सत्संग किस समय है?",
      "audio": {
        "uri": "s3://satsandesh-audio-dev/renders/abc123-hi.wav",
        "format": "wav_pcm16",
        "duration_ms": 2100,
        "sample_rate_hz": 22050
      },
      "model_version_translate": "indictrans2-distilled@1",
      "model_version_tts": "indic-tts-vits@1",
      "duration_ms": 310.5,
      "degraded": { "active": false, "reason": "none", "detail": null }
    },
    {
      "language": "te",
      "text": "నమస్తే, ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
      "audio": { "...": "..." },
      "model_version_translate": "indictrans2-distilled@1",
      "model_version_tts": "indic-tts-vits@1",
      "duration_ms": 295.8,
      "degraded": {
        "active": true,
        "reason": "tts_skipped",
        "detail": "VRAM headroom reserved for ASR queue"
      }
    }
  ],
  "degraded": {
    "active": true,
    "reason": "tts_skipped",
    "detail": "at least one target language degraded — see per-result detail"
  }
}
```
**Read `degraded` on each `RenderResult`, not just the top-level one.** One
target language can independently lose its audio (e.g. TTS skipped under VRAM
pressure) while the others in the same fan-out succeed. The top-level `degraded`
is only a quick "did anything in this batch degrade" summary.

### `POST /v1/moderate` — English pivot → moderation decision

Request (`ModerationRequest`):
```json
{ "contract_version": "0.1.0", "text": "Namaste, what time is today's satsang?" }
```

Response (`ModerationDecision`):
```json
{
  "contract_version": "0.1.0",
  "label": "D_DISPUTATIONAL",
  "confidence": 0.81,
  "action": "NUDGE",
  "rationale": "Message reads as argumentative in tone; nudging sender toward gentler phrasing before it reaches the circle.",
  "nudge_text": "దయచేసి మృదువుగా చెప్పండి.",
  "nudge_language": "te",
  "policy_version": "policy@2026-08-13",
  "model_version": "qwen2.5-7b-instruct-q4@1",
  "degraded": { "active": false, "reason": "none", "detail": null }
}
```

- `label`: `A_DEVOTIONAL` / `B_ORGANIZATIONAL` / `C_PERSONAL` / `D_DISPUTATIONAL` / `E_HARMFUL`
- `action`: `ALLOW` / `NUDGE` / `HOLD` / `BLOCK` — independent of `label` and
  `confidence`. A low-confidence call can always route to `HOLD` regardless of
  what label the classifier guessed.
- `rationale` is English, for moderators/ops. **Do not show it to the sender.**
- `nudge_text` / `nudge_language`: set when `action` is `NUDGE` (or `HOLD`, if a
  nudge is shown while a message awaits review). This is sender-facing and is in
  the **sender's own language**, not English — it will usually differ from
  `rationale`. Both are `null` when no nudge is produced (e.g. `ALLOW`/`BLOCK`).

## Errors

Any pipeline stage can fail with a `PipelineError`:
```json
{
  "contract_version": "0.1.0",
  "code": "OUT_OF_MEMORY",
  "message": "Failed to allocate VRAM for Indic-TTS while ASR and moderation models were resident.",
  "stage": "render.tts",
  "detail": { "requested_mb": 1800, "available_mb": 640 }
}
```
`code` is one of: `MODEL_LOAD_FAILED`, `OUT_OF_MEMORY`, `UNSUPPORTED_LANGUAGE`,
`AUDIO_FETCH_FAILED`, `TIMEOUT`, `INTERNAL_ERROR`.

The mock server does not currently emit `PipelineError` responses — it always
succeeds with canned data. If you need to test your error-handling path against
this shape, construct a `PipelineError` directly from the contract in your own
test, or ask (see Open Questions — error injection in the mock server).

## Golden fixtures

`tests/fixtures/*.json` — one canonical example of every model above, checked in
and asserted (`tests/test_golden_fixtures.py`) to still parse into its model. If
a PR changes a field shape, this test fails instead of the drift being caught by
memory. Regenerate after a deliberate contract change:
```bash
PYTHONPATH=../.. ./.venv/Scripts/python.exe tools/generate_fixtures.py
```

## Development

```bash
cd services/ai
./.venv/Scripts/python.exe -m pytest tests/            # unit + contract tests
./.venv/Scripts/python.exe -m mypy -p contracts.ai -p services.ai   # from repo root, MYPYPATH set
./.venv/Scripts/python.exe -m ruff check contracts/ai services/ai  # from repo root
```

See `DECISIONS.md` for design rationale and `OPEN_QUESTIONS.md` for what still
needs the team's sign-off at the Week-1 contract meeting.
