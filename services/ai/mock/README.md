# AI Mock Server

`services/ai/mock/app.py` is a fast, deterministic stand-in for every
`services/ai/` endpoint (ASR, pivot translation, render/TTS, moderation).
M2 builds against this from Week 2 onward; real services replace pieces of it
one at a time, with `services/ai/speech/` (real ASR) landing in Week 8.

By default the mock always takes the happy path — canned responses that
validate against the real Pydantic contracts, with configurable artificial
latency. Two opt-in request headers let you exercise error and
degraded-mode handling *now*, before the real services exist, so that code
written against this mock keeps working unchanged once it's pointed at the
real thing.

## Running it

```
services\ai\.venv\Scripts\python.exe -m uvicorn services.ai.mock.app:app --reload
```

## Endpoints

| Method | Path             | Purpose                                   |
|--------|------------------|--------------------------------------------|
| GET    | `/health/live`   | Liveness — always 200 once the process is up |
| GET    | `/health/ready`  | Readiness — always 200 immediately (no model to warm up) |
| POST   | `/v1/transcribe` | Mock ASR                                  |
| POST   | `/v1/pivot`      | Mock pivot translation                    |
| POST   | `/v1/render`     | Mock fan-out translate + TTS per target language |
| POST   | `/v1/moderate`   | Mock moderation decision                  |

### `GET /health/live`

```
curl http://localhost:8000/health/live
```

```json
{"status": "live"}
```

### `GET /health/ready`

Field names match `services/ai/speech/app.py`'s real `/health/ready` shape
exactly, so M2's health-check logic written against the mock keeps working
unchanged when pointed at the real ASR service in Week 8. The mock has no
model to load or warm up, so it is always `ready` with zero durations.

```
curl http://localhost:8000/health/ready
```

```json
{
  "status": "ready",
  "model_version": "mock@0.1.0",
  "load_duration_ms": 0.0,
  "warmup_duration_ms": 0.0
}
```

## Opt-in headers

Nobody calling the mock normally will notice these — they only change
behavior when set explicitly.

### `X-Mock-Latency-Ms` (all endpoints)

Overrides the `MOCK_LATENCY_MS` env var default (50ms) for a single request,
so one client can simulate a slow ASR call and a fast pivot call in the same
test run. Already existed before this change; documented here for
completeness.

```
http POST :8000/v1/pivot text="hello" source_language=en X-Mock-Latency-Ms:150
```

### `X-Mock-Error: <ErrorCode>` (all endpoints)

Makes the endpoint return a `PipelineError` with the given code at `422`
instead of its normal canned response — the same status code convention
`services/ai/speech/app.py` uses for decode/format errors. Valid values are
the `contracts.ai.errors.ErrorCode` members: `MODEL_LOAD_FAILED`,
`OUT_OF_MEMORY`, `UNSUPPORTED_LANGUAGE`, `AUDIO_FETCH_FAILED`, `TIMEOUT`,
`INTERNAL_ERROR`. An unrecognized value returns a `400` explaining why,
never a silent fallback to the happy path.

```
curl -X POST http://localhost:8000/v1/transcribe \
  -H "Content-Type: application/json" \
  -H "X-Mock-Error: AUDIO_FETCH_FAILED" \
  -d '{"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}}'
```

```json
{
  "contract_version": "0.1.0",
  "code": "AUDIO_FETCH_FAILED",
  "message": "mock-injected error via X-Mock-Error header (code=AUDIO_FETCH_FAILED)",
  "stage": "transcribe.mock_injected",
  "detail": {"injected": true}
}
```

Invalid value example:

```
curl -X POST http://localhost:8000/v1/transcribe \
  -H "Content-Type: application/json" \
  -H "X-Mock-Error: NOT_A_REAL_CODE" \
  -d '{"audio": {"uri": "file:///tmp/a.wav", "format": "wav_pcm16"}}'
```

```json
{
  "error": "invalid_mock_error_code",
  "message": "X-Mock-Error value 'NOT_A_REAL_CODE' is not a valid ErrorCode. Valid values: MODEL_LOAD_FAILED, OUT_OF_MEMORY, UNSUPPORTED_LANGUAGE, AUDIO_FETCH_FAILED, TIMEOUT, INTERNAL_ERROR",
  "valid_values": ["MODEL_LOAD_FAILED", "OUT_OF_MEMORY", "UNSUPPORTED_LANGUAGE", "AUDIO_FETCH_FAILED", "TIMEOUT", "INTERNAL_ERROR"]
}
```

Status code: `400`.

### `X-Mock-Degraded: true` (`/v1/render` only)

`/v1/render` fans out to one `RenderResult` per target language, and each
result already carries its own `degraded` field (a fan-out can partially
degrade — e.g. TTS skipped for one language under VRAM pressure while the
others succeed). By default every result is `degraded.active: false`. Set
this header to make the first result in the response come back with
`degraded.active: true` and a plausible reason/detail, so M2 can test
partial-degradation handling without a GPU.

```
http POST :8000/v1/render pivot_text="Good morning" target_languages:='["hi", "te"]' X-Mock-Degraded:true
```

```json
{
  "contract_version": "0.1.0",
  "results": [
    {
      "language": "hi",
      "text": "सुप्रभात, आपका दिन शांतिपूर्ण हो।",
      "audio": {"uri": "mock://audio/...", "format": "wav_pcm16", "duration_ms": 1800, "sample_rate_hz": 22050},
      "model_version_translate": "indictrans2-distilled@mock",
      "model_version_tts": "indic-tts-vits@mock",
      "duration_ms": 50.0,
      "degraded": {
        "active": true,
        "reason": "tts_skipped",
        "detail": "mock-injected via X-Mock-Degraded header: TTS skipped to simulate VRAM pressure shedding load"
      }
    },
    {
      "language": "te",
      "...": "...",
      "degraded": {"active": false, "reason": "none", "detail": null}
    }
  ],
  "degraded": {"active": false, "reason": "none", "detail": null}
}
```

Any other endpoint's `degraded` field stays `DegradedMode.ok()` — there's no
equally natural per-language/per-stage split to inject degradation into for
`/v1/transcribe`, `/v1/pivot`, or `/v1/moderate`.

## `/v1/transcribe` stage timings

The canned `stage_timings` split is fabricated (there's no real model
running), but it's shaped to match what Phase 2's real measurements showed:
inference dominates almost completely, with decode and postprocess both
near-zero. So the mock splits artificial latency roughly 1% preprocess / 98%
inference / 1% postprocess, not an even or arbitrary split — a consumer
skimming mock responses should come away with the right mental model of
where AI-lane latency actually comes from.
