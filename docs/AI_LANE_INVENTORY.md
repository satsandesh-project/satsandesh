# AI Lane Inventory — starting state for Week 5 (ASR bring-up)

**Date:** 2026-09-19
**Repo state when written:** `origin/main` @ `133bf63e74d2bb6034da0b2a855dd5e4c0c7b7aa`
(local `main` fast-forwarded to match; working tree otherwise clean)
**Scope of this document:** `contracts/ai/`, `services/ai/`, and the shared
mechanisms they depend on. Written before any Week 5 code exists, as the baseline
Phase 1 builds on.

This is a *record of what is actually there*, read fresh. Where something is a
stub, stale, or not wired up, it says so.

---

## 1. `contracts/ai/` — the Pydantic contract package

Nine files, all real code, no placeholders left unfinished. The package is a pure
schema layer: it declares shapes and validates them, and contains no inference or
service logic at all.

- **`common.py`** — the foundation. Holds `CONTRACTS_VERSION = "0.1.0"`,
  `VersionedModel` (the base that stamps `contract_version` onto every top-level
  payload), `AudioFormat` (`wav_pcm16` / `ogg_opus` / `mp3`), `AudioRef`,
  `StageTiming`, and `DegradedMode`/`DegradedReason`. `DegradedMode` is the
  load-shedding signal: it lets a response say "this is a partial result" rather
  than silently looking complete when the GPU dropped TTS.
- **`language.py`** — `LanguageCode`, a *closed* enum of exactly `en`/`hi`/`te`.
  Deliberately BCP-47 rather than any model's native codes, because no two models
  in the pipeline agree (faster-whisper's short codes vs IndicTrans2's FLORES-200
  `tel_Telu` vs TTS voice ids); per-model mapping is pushed into the service
  layer. Tamil and Kannada are intentionally absent.
- **`transcribe.py`** — `TranscribeRequest` (an `AudioRef` plus optional
  `language_hint`) and `TranscribeResponse` (`text`, `detected_language`,
  `model_version`, `duration_ms`, `stage_timings`, `degraded`). **This is the
  contract Week 5 has to satisfy for real.**
- **`pivot.py`** — `PivotRequest`/`PivotResponse` for any-language → English
  pivot text. Minimal and symmetric with transcribe.
- **`render.py`** — the most developed shape. `RenderRequest` fans out one pivot
  text to many `target_languages`, with a validator that rejects an empty list
  and silently de-duplicates while preserving first-occurrence order. Each
  `RenderResult` carries its *own* `degraded`, so one language can lose audio
  while others succeed.
- **`moderation.py`** — `ModerationRequest` and `ModerationDecision` (five labels
  `A_DEVOTIONAL`…`E_HARMFUL`, four actions `ALLOW`/`NUDGE`/`HOLD`/`BLOCK`,
  `confidence`, an ops-facing English `rationale`, and a separate sender-facing
  `nudge_text`/`nudge_language`). Note this is **M4's subject matter living in my
  package** — see §5.
- **`errors.py`** — `PipelineError` with a six-value `ErrorCode` enum. Real, but
  nothing currently *emits* it (see §2).
- **`envelope.py`** — **the one genuine stub.** Its own docstring says
  "PROPOSAL, NOT A DEPENDENCY": it sketches `{type, id, ts, payload}` and a
  `MessageType` enum so my tests can round-trip standalone, but no request or
  response model imports it, and the gateway owner defines the real envelope.
  Anything Week 5 does must not start treating this as settled.
- **`__init__.py`** is empty; `.gitignore` covers `__pycache__`.

## 2. `services/ai/mock/app.py` — the mock server

144 lines, one FastAPI app, four POST endpoints, no GPU required. Every response
is constructed as a real Pydantic model instance rather than hand-written JSON,
so anything that passes the contract tests is what callers actually receive.
Latency is simulated: it sleeps `MOCK_LATENCY_MS` (default 50ms) or whatever a
per-request `X-Mock-Latency-Ms` header says, so one test run can mix a slow ASR
call with a fast pivot call.

- `POST /v1/transcribe` — accepts `TranscribeRequest`; **ignores the audio
  entirely** and returns canned text chosen by `language_hint` (defaulting to
  Hindi when the hint is absent, rather than doing any detection). Fabricates a
  10/80/10 preprocess/inference/postprocess `stage_timings` split from the
  latency figure. Model version string is `faster-whisper-small-int8@mock`.
- `POST /v1/pivot` — returns the canned English sentence regardless of input
  text, echoing back the caller's `source_language`.
- `POST /v1/render` — the only endpoint that really varies with input: it maps
  over `target_languages` and emits one `RenderResult` each, with a
  `mock://audio/<uuid>.wav` URI. Falls back to echoing `pivot_text` for any
  language missing from the canned table.
- `POST /v1/moderate` — first-match keyword scan over a three-entry table
  (`urgent`→ALLOW, `argue`→NUDGE, `hate`→BLOCK), defaulting to
  `A_DEVOTIONAL`/`ALLOW`. Confidence is hardcoded at `0.93`. The Telugu nudge
  text is hardcoded too — it is emitted regardless of the sender's actual
  language, which is a mock artifact, not the intended behaviour.

Not present: any `/health` endpoint, any way to force a `PipelineError` or a
non-2xx response, and any degraded-mode response — every endpoint always returns
`DegradedMode.ok()`. So no downstream consumer has yet been able to exercise its
error or degraded-mode path against this server.

## 3. `services/ai/tests/fixtures/` — golden fixtures

Nine JSON files, one canonical example per top-level model: request+response for
transcribe, pivot, render, and moderation, plus `pipeline_error.json`. They are
machine-generated, not hand-written — `tools/generate_fixtures.py` builds each
from the live contract models and writes them sorted-key, indent-2, UTF-8
unescaped, so diffs stay readable and stable. `tests/test_golden_fixtures.py`
parametrises over all nine and asserts a full parse-and-round-trip
(`model_dump(mode="json") == raw`), which catches a renamed, retyped, or dropped
field.

Content is realistic rather than filler: the render fixture deliberately encodes
a *partial* degradation (Hindi fine, Telugu `tts_skipped`) and the error fixture
encodes a VRAM exhaustion during TTS. Worth noting for Week 5:
`transcribe_request.json` describes a 30s, 16kHz, `wav_pcm16` file at a
`file:///tmp/sample.wav` URI — **there is no actual audio file anywhere in the
repo.** The fixtures are schema examples only; Week 5 needs real audio samples,
and none exist yet.

## 4. `CONTRACTS_VERSION` — where it is stamped, and how a mismatch surfaces

Stamped in exactly one place: `contracts/ai/common.py` defines
`CONTRACTS_VERSION = "0.1.0"`, and `VersionedModel` sets `contract_version` as a
**default field value** on every top-level payload. There is a deliberately
independent twin in `contracts/chat/common.py` (also `0.1.0`); the two do not
import each other, by design.

How a mismatch would actually surface — this matters, and the answer is weaker
than the docs imply:

- **Not at runtime, at all.** `contract_version` is a plain `str` with no
  validator and no equality check anywhere in the codebase. A caller sending
  `"0.1.0"`, `"9.9.9"`, or `"banana"` is accepted identically. Nothing compares
  an incoming value to the local constant, and nothing logs or rejects on
  difference. The field is *informational* — it tells a human which shape they
  are looking at; it does not enforce anything.
- **In tests, yes** — but only locally. `test_golden_fixtures.py` fails if a
  shape changes without regenerating fixtures, and `test_contracts_common.py`
  asserts the constant is a dotted string.
- **In CI, no — and this is the finding worth acting on.** The workflow runs a
  bare `pytest` from the repo root, and the root `pyproject.toml` sets
  `testpaths = ["tests"]`. Verified by collection: that command collects exactly
  **one** test, `tests/test_placeholder.py::test_placeholder`. All 66 tests under
  `services/ai/tests/` — including the golden-fixture drift check — **never run
  in CI.** The README's claim that drift "fails in CI instead of being caught by
  memory later" is not true as configured. (CI's `ruff check .` and
  `ruff format --check .` *do* cover these paths; it is only the tests that are
  skipped.) Separately, CI installs only `ruff` and `pytest`, so even if
  `testpaths` were widened, collection would fail on the missing `pydantic` and
  `fastapi` imports until the workflow also installs the service's dependencies.

Local baseline as of today: `66 passed` under `services/ai/`.

## 5. `.github/CODEOWNERS` — the AI lane is not actually covered

Read carefully, and the answer to "are M4's moderation work and my speech work
distinguished?" is: **neither one is represented at all.**

- There is **no entry for `services/ai/` and no entry for `contracts/ai/`.** Both
  fall through to the `*` fallback line, which requests review from all four
  members on every AI PR. So the two workstreams do not *collide* in CODEOWNERS
  so much as they are both invisible to it.
- The file does have a "CONTESTED" entry for **`/ai-services/`** listing
  `@veerendrakosuri @Master-ff`. That is a **different directory** — and it still
  exists in the tree, with 8 tracked files (a `Dockerfile`, `main.py`, a health
  check stub, requirements files). It is not where my work lives. So the repo
  currently has two AI directories, and CODEOWNERS guards the one that is not the
  contract lane. `ai-services/` was last touched 2026-08-31 by veerendrakosuri;
  `services/ai/` was last touched 2026-08-14 by me.
- Authorship is unambiguous and git-verifiable: `services/ai/` + `contracts/ai/`
  is 5 commits by Sandesh and 1 by kpspyolo024.

The file's own header says the owners listed "reflect who actually authored what
is in `main` today" and should be updated once roles settle — so the omission
reads as an oversight from when the directory moved, not a decision. Consequence
for Week 5: any PR I open under `services/ai/` currently pulls in all four
reviewers, and nothing distinguishes a speech change from a moderation change.
**Fixing CODEOWNERS is outside this lane's scope and needs the team** (the file
is owned by all four members) — raising it is the action, not editing it.

---

## Summary of things Week 5 should not assume

1. `contracts/ai/envelope.py` is a proposal; the gateway's real envelope is still
   unconfirmed.
2. `AudioRef.uri` is an unvalidated string — no storage layer is decided, and no
   real audio file exists in the repo to test against.
3. The mock's `/v1/transcribe` never looks at the audio; there is no working
   audio path to inherit.
4. `contract_version` enforces nothing at runtime, and the AI test suite does not
   run in CI.
5. CODEOWNERS does not cover `services/ai/` or `contracts/ai/`; the stale
   `ai-services/` directory is what is guarded instead.
6. There is no CUDA GPU on this development machine (see the Week 5 environment
   notes) — the contracts' VRAM/degraded-mode vocabulary describes a deployment
   target, not this laptop.
