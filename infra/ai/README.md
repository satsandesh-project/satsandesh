# The AI services in compose (`docker-compose.ai.yml`)

The real ASR, moderation, MT and render services, wired to the gateway's
pipeline orchestrator (`services/gateway/ORCHESTRATOR_DESIGN.md`).

It is an **override file**, not part of the default stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.ai.yml up -d
```

A plain `docker compose up` — and staging — is exactly what it was. The override
adds the services below, switches the gateway's pipeline **on**, and points it at
them. Read "What was and was not proven" before relying on any of it.

## Services

| Service | Image | Needs | State |
|---|---|---|---|
| `speech` | `infra/ai/Dockerfile.speech` — faster-whisper `small`, ffmpeg | nothing (public weights, downloaded on first start) | **built and run** |
| `moderation` | `infra/ai/Dockerfile.moderation` — **stub backend** | nothing | **built and run** (stub only) |
| `ai-mock` (profile `mock`) | `infra/ai/Dockerfile.mock` | nothing | **built and run** |
| `mt` | `infra/ai/Dockerfile.mt` — IndicTrans2 indic→en | **`HF_TOKEN`** (gated model) | written, **never built or run** |
| `render` | `infra/ai/Dockerfile.render` — IndicTrans2 en→indic + Piper | **`HF_TOKEN`**, a *separate* gate from `mt`'s | written, **never built or run** |

No `HF_TOKEN` exists on the shared server, so `mt` and `render` could not be run.
Their Dockerfiles follow the same pattern as the three that were, but treat them as
unverified until someone with a token builds them.

**Secrets:** put `HF_TOKEN` in `.env` **on the server**. Never commit it, never
paste it into a chat. (It is deliberately not enforced with `${HF_TOKEN:?}` in the
override file — compose interpolates every service when the file loads, which would
break a run that doesn't start `mt`/`render`; the services themselves fail fast with
a clear error when it is empty.)

## Wiring

- **Network:** internal only. No AI service publishes a port, and nothing was added
  to `infra/caddy/Caddyfile`. The real ASR opens whatever local path a request names,
  with no authentication: it must never be reachable from outside. (The existing
  `/ai/*` Caddy route still points at the Week-1 health-check stub.)
- **Audio in:** the gateway's `media_data` volume is mounted **read-only** into
  `speech` at `/ai-media`; the gateway sends `file:///ai-media/<id>.bin`
  (`AI_AUDIO_MOUNT_ROOT`). The real ASR does not understand `media:<id>`.
- **Audio out:** `render` writes `.wav` files to a `render_output` volume the gateway
  mounts **read-only** at `/render-output` (`AI_RENDER_AUDIO_ROOT`); the gateway reads
  them by file name and stores them as media.
- **No `depends_on` on the AI services:** models take minutes to load, and the gateway
  must start and keep serving chat regardless. The pipeline retries a not-ready service
  with backoff, and a message waits behind its pipeline rather than being lost.
- **`AI_ACCEPT_WEBM_AS_OGG_OPUS` defaults to `false`**, so with the pipeline on every
  real browser voice note is *held* until M3 decides how `webm_opus` should reach the
  ASR (`services/gateway/OPEN_QUESTIONS.md` #2, #19).

## Running without the gated models

```bash
AI_PIVOT_URL=http://ai-mock:8001 AI_RENDER_URL=http://ai-mock:8001 \
docker compose -f docker-compose.yml -f docker-compose.ai.yml --profile mock \
    up -d postgres gateway speech moderation ai-mock
```

The wiring is real; the pivot text is canned and renderings come back text-only.

## What was and was not proven

`infra/ai/e2e/run_proof.sh` brings the stack up on a throwaway compose project
(its own Postgres and volumes) and runs four scenarios. Run on the shared server,
2026-10-04, 16 cores, CPU only. **Real:** the gateway, the ASR (faster-whisper
`small`, real WebM/Opus decoded by ffmpeg, the file opened from the shared volume),
and the moderation service (stub backend). **Mock:** MT and render.
`UNDO_WINDOW_SECONDS=0`, so delivery is attempted before the pipeline can finish.

| Scenario | What happened |
|---|---|
| **A. real speech** | Right after sending: message `pending`, pipeline `pending`, job `running`. The recipient polled four times and saw nothing, then saw the message **~3.9 s after the send, already carrying the ASR transcript** (*"Hello everyone. When is the sat-san today? Please bring some flowers."*) and its rendering. Delivery fired at t=0 and could only have skipped; the job delivered it. **The gate works on the real stack.** |
| **B. digital silence** | **Not held.** The real ASR returned the word **"You"** for 3 s of silence, so the pipeline treated it as a note with a transcript, the classifier allowed it, and it was delivered. (An earlier run's sine tone came back as **"Beep"**.) |
| **C. SIGKILL mid-job** | At the kill the job was `running` (attempt 1) and the message `pending`. After the restart the 20 s lease expired, the job was reclaimed (`attempts=2`), and the message was delivered ~24 s after the send with its transcript — **exactly one** classifier ruling, **no** duplicate jobs. The recipient saw nothing during the outage. |
| **D. moderation down** | With the moderation container stopped: the job retried, died after 3 attempts (`JOB_MAX_ATTEMPTS=3`), and the message became `held` / `pipeline_state=failed` with a `SYSTEM` HOLD event giving the reason. The recipient never saw it. It appeared in the moderator's queue; after a moderator **released** it, the recipient received it. **Nothing was delivered unmoderated.** |

### Findings from the proof

1. **The real ASR hallucinates words from non-speech** (a tone → "Beep", silence →
   "You"). The orchestrator's "no speech recognised → hold" path therefore never fires
   with this ASR, and a silent or noisy note is delivered with a junk transcript. That is
   `services/ai/speech/` (M3): the engine's VAD / no-speech settings are the place to
   look. Not fixed here.
2. **A released pipeline-failure hold has no renderings.** In D the pipeline died at the
   moderation stage, before render ran, and a release flips the status without re-running
   anything — so receivers got the original only. Worth deciding whether a release should
   re-run the missing stages.
3. A note on the setup, not a result: three seconds after the `SIGKILL` the gateway was
   still `exited` (`restarts=0`) despite `restart: unless-stopped`, so the proof starts it
   explicitly. Whether Docker would have restarted it later was not observed.

### Limits — what this does NOT show

- **MT and render are mocked**, so nothing here says anything about translation or
  synthesized speech. `render`'s own README says its MT half has never been run against
  the real model.
- **Moderation is the keyword stub**, which its README says is not fit for the pilot; the
  real classifier (llama.cpp + a ~2 GB Qwen GGUF) was not run.
- **The recordings are synthesized** (a robotic voice, WebM muxed by ffmpeg) — not a
  person, not Chrome's `MediaRecorder`. They prove the wiring, not accuracy on an elder's
  voice.
- One run on one CPU server; **no load, no concurrency**, no CPU saturation (Week 6's
  saturated-CPU proof was for the media store, not for this).
- Python dependencies in the images follow `services/ai/pyproject.toml`, which uses lower
  bounds (`>=`), not pins: an image built next month may resolve newer wheels.
- `docker-compose.ai.yml` has been validated with `docker compose config` and run for the
  three services above; it has **not** been run alongside the rest of the default stack
  (Caddy, the elder app).

## Still not decided (M3's / yours)

Which ASR is the real one (`speech` vs `speech_indicconformer` — the latter is **not**
in this file; it cannot auto-detect a language, and an undeclared voice note would be
rejected and held), how `webm_opus` reaches the AI services, and whether a shared volume
is the permanent way they read audio (`OPEN_QUESTIONS.md` #2, #11). Real JWT auth, which
must land before the moderator routes are reachable by anyone real, and `GET/PATCH
/me/settings`, without which the pipeline renders only Telugu for everyone, are both
prerequisites for switching the pipeline on for real users.

## Re-running the proof

```bash
cp ~/veerendra/.env .env        # POSTGRES_* and GATEWAY_* only; never commit it
infra/ai/e2e/run_proof.sh       # ~10 min first run (model download), then ~5 min
```
