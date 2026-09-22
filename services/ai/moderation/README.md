# services/ai/moderation — the stewardship classifier

**Owner:** M4 Sainathan (Stewardship, quality & pilot)
**Contract:** `contracts/ai/moderation.py` — `POST /v1/moderate`,
`ModerationRequest` in, `ModerationDecision` out. Same shape the mock
(`services/ai/mock/app.py`) already serves, so the orchestrator swaps the
URL and nothing else on Week-8 Monday.
**Status:** Week-5 scaffold. Everything except the model is real: policy
loading, prompt construction, strict parsing, confidence gating, fail-closed
behaviour, bounded input, serialised inference with a timeout, health
endpoints, 45 tests. The model backend ships in two flavours — a keyword
**stub** (default; no weights, for tests and the dev loop) and **llama.cpp**
(Qwen2.5 3B Instruct, 4-bit, CPU; the Week-6 bring-up).

## Run

From the repository root:

```bash
cd services/ai && python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

```bash
PYTHONPATH=. services/ai/.venv/Scripts/python.exe -m uvicorn services.ai.moderation.app:app --port 8003
```

Then `GET http://localhost:8003/health/ready` tells you which backend and
which policy version are live, and how many exemplars each category has.

```bash
curl -s -X POST localhost:8003/v1/moderate -H "content-type: application/json" -d "{\"text\": \"Bhajans this Thursday at 6 pm\"}"
```

Ports: 8001 mock, 8002 ASR, **8003 moderation**.

## What happens to a message

```
text ──► bound (empty? too long? → 422 PipelineError)
     ──► prompt.build_messages(policy, text)     policy from docs/policy-taxonomy.md
     ──► backend.complete(messages)              serialised; timeout
     ──► prompt.parse_model_output(raw)          strict JSON → RawVerdict | None
     ──► decide.decide(verdict, policy, ...)     the gate (below)
     ──► ModerationDecision                      stamped policy_version + model_version
```

### The gate (`decide.py`)

| Model said | Confidence | Action |
|---|---|---|
| A or B | ≥ hold threshold | `ALLOW` |
| C | ≥ hold threshold | `NUDGE` + sender notice |
| D | any | `HOLD` + sender notice |
| E | ≥ block threshold | `BLOCK` + sender notice, moderator alerted |
| E | between hold and block thresholds | `HOLD` — a wrong block is the most visible failure we can have |
| anything | < hold threshold | `HOLD` — nothing is allowed, nudged or blocked on an unsure guess |
| *no verdict* (error, timeout, unparseable, busy) | — | `HOLD`, `confidence 0.0`, `degraded.active = true`, `reason = model_fallback` |

Defaults: hold 0.70, block 0.85 (`MOD_HOLD_THRESHOLD`, `MOD_BLOCK_THRESHOLD`).
Every non-`ALLOW` carries `nudge_text` — the sender is always told, nothing
is ever silently dropped (proposal §7.3, §15).

### The policy document is the source of truth

`docs/policy-taxonomy.md` is parsed at startup (`policy.py`):

- the **taxonomy table** → categories and default actions
- `## Exemplars` → `### A` … `### E` bullet lists → the prompt's examples
  (up to 12 per category, the proposal's "a dozen")
- `## Sender notices` → `### C — nudge`, `### D — held`, `### E — blocked`
  blockquotes → `nudge_text`
- `## Changelog` → the latest dated line → `policy_version = policy@YYYY-MM-DD`

So a policy change is a docs PR the organisation's liaison approves, and
the version stamped on every decision changes exactly when the document
does. Until the taxonomy workshop's output lands the exemplar section is
empty; the service starts anyway, logs a warning, and `/health/ready`
reports `has_exemplars: false`. **It is not fit for the pilot in that
state** — the prompt is running on category descriptions alone.

The expected post-workshop format is exactly what
`moderation/tests/fixtures/policy_sample.md` looks like.

### Prompt-injection posture (`prompt.py`)

The message is placed between explicit delimiters as *content to
classify*; the system prompt says instructions inside it are part of the
message and themselves a signal for D/E; a message that tries to close the
delimiter early is neutralised. The model's reply is parsed strictly —
anything that isn't `{"label", "confidence", "rationale"}` with a known
label and a confidence in `[0, 1]` is *no verdict* → `HOLD`. Red-team round
1 (Week 9) attacks exactly this.

### Bounded and serialised (`app.py`)

- `MOD_MAX_TEXT_CHARS` (4000): longer → 422, never truncated silently.
- `MOD_MAX_CONCURRENT` (1): a semaphore around inference. A request that
  can't get a slot within `MOD_TIMEOUT_S` (20 s) is `HOLD`/`degraded`
  ("busy"), and a slot is released only when the inference actually
  finishes — a timed-out call still owns the CPU.
- The route is a plain `def`, so a 10-second CPU inference never blocks the
  event loop and `/health/live` keeps answering.

## Settings

| Variable | Default | |
|---|---|---|
| `MOD_BACKEND` | `stub` | `stub` or `llama_cpp` |
| `MOD_MODEL_PATH` | — | GGUF path; required for `llama_cpp` |
| `MOD_POLICY_PATH` | `docs/policy-taxonomy.md` | |
| `MOD_HOLD_THRESHOLD` | `0.70` | |
| `MOD_BLOCK_THRESHOLD` | `0.85` | must be ≥ hold |
| `MOD_MAX_TEXT_CHARS` | `4000` | |
| `MOD_MAX_CONCURRENT` | `1` | |
| `MOD_TIMEOUT_S` | `20` | |
| `MOD_PORT` | `8003` | |

An invalid value fails at import, with the variable named.

## Week-6 bring-up (llama.cpp)

```bash
services/ai/.venv/Scripts/python.exe -m pip install -e ".[moderation]"
```

Download a `Qwen2.5-3B-Instruct` Q4_K_M GGUF (~2 GB) to a path outside the
repository, then:

```bash
MOD_BACKEND=llama_cpp MOD_MODEL_PATH=/path/to/qwen2.5-3b-instruct-q4_k_m.gguf PYTHONPATH=. services/ai/.venv/Scripts/python.exe -m uvicorn services.ai.moderation.app:app --port 8003
```

`LlamaCppClassifier` runs at temperature 0 with a JSON schema constraint,
so the parser's strictness is belt-and-braces, not the only defence. Model
weights are never committed; pin the exact file (name + sha256) in this
README when the bring-up picks one. Not covered by the test suite — mark
any test that needs weights `@pytest.mark.integration` (already configured
to skip by default in `services/ai/pyproject.toml`).

## Tests

```bash
cd services/ai && ./.venv/Scripts/python.exe -m pytest moderation/tests -q
```

45 tests, no weights, ~2 s: policy parsing (sample + the real committed
document + fallbacks), prompt delimiting and strict parsing, the gate
table above exhaustively, and the FastAPI path end to end including every
failure mode (backend error, garbage output, timeout, busy, empty,
oversized, not ready).

## Open questions (for the contract owner, M3)

1. **`ModerationRequest` has no sender language.** Notices are English
   masters from the policy document; `nudge_language` is always `en`.
   Either add `sender_language: LanguageCode` to the request so this
   service can pick the translated notice, or the orchestrator translates
   `nudge_text` downstream. The former is one field; proposing it for the
   Week-7 contract bump.
2. **`ErrorCode` has no `INVALID_INPUT`.** Empty/oversized text is returned
   as `INTERNAL_ERROR` with `stage="moderate.validate"`, which is
   misleading. One enum value fixes it.
3. **Fail-closed label.** With no verdict the decision must still carry a
   `label`; this service uses `D_DISPUTATIONAL` + `confidence 0.0` +
   `degraded`. If the console would rather see an explicit `UNCLASSIFIED`,
   that's a contract change too.
