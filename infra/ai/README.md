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

- **MT and render are mocked in this run**, so nothing here says anything about
  translation or synthesized speech; the next section is the run with the real ones.
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

## The real MT and render proof (`run_proof_real.sh`)

Run 2026-10-04 on the same server (CPU only). **Real:** the gateway, the ASR
(faster-whisper `small`), MT (`indictrans2-indic-en-dist-200M`), render
(`indictrans2-en-indic-dist-200M` + Piper `hi_IN-rohan` / `te_IN-maya`). **Stub:** moderation.
`AI_ACCEPT_WEBM_AS_OGG_OPUS=true` (the stopgap), `UNDO_WINDOW_SECONDS=0`. The "spoken" notes are
**Piper voices** (the render service synthesizes a Hindi and a Telugu sentence, ffmpeg muxes
them to WebM/Opus): not a person, not Chrome's `MediaRecorder`. A circle with three readers:
Telugu, English, Hindi. The rendering audio is fetched through the gateway **as each reader**
and measured, because `audio != null` does not mean it plays.

| Note | What happened |
|---|---|
| **Hindi** (spoken: *"There is a bhajan at the temple at seven this evening. Everyone is welcome."* in Hindi) | Visible to all three readers **~6 s after the send**, `sent` / `pipeline=complete`, job `done` on attempt 1. Transcript (hi) had small errors (*"साथ बजे"* for *"सात बजे"*, *"सागत"* for *"स्वागत"*) but the English pivot kept the meaning: *"There is a bhajan in the temple this evening, everyone is welcome."* Renderings: **en** (text only, see below) and **te** (Telugu text + audio: `200 audio/wav`, 5.0 s, RMS 4519 — not silence). The reader of the source language (Hindi) got no `hi` rendering and reads the original. |
| **Telugu** (same sentence in Telugu) | Completed the same way (~6.6 s), renderings **en** and **hi** (Hindi audio `200 audio/wav`, 4.5 s, RMS 6499), no `te` for the Telugu reader. **But the content was wrong, with no signal to anyone:** the transcript came back in **Devanagari script** (*"इस आईन्त्रम येडू गंटलकू …"*) labelled `te`; the pivot was *"All are welcome to bajana hoon on this day and seven o'clock in the morning."*; the Hindi rendering says the same wrong thing. The classifier allowed it (the text is harmless), so it was delivered. |

"A note becomes N renderings" is shown with real models: each note became the renderings
for the languages its recipients read, minus its own.

### Findings

1. **The first run of the Telugu note failed, and it was a bug of mine** (fixed in #101). The
   handler held a database transaction open during the ASR call; the gateway's connections are
   killed after 30 s idle-in-transaction; the same recording took **37.2 s** in isolation. Every
   attempt died on the transcript write, the job went `dead`, and the message was **held**
   (fail-closed worked: nothing was delivered). **The rerun's ASR was fast, so it did not hit
   the slow path again**: the fix is shown by a unit test and six mutations, not by a repeat of
   the real >30 s call. Why one recording took 37 s once and ~6 s another time is **not
   investigated**.
2. **The real ASR is unreliable on Telugu** (and mediocre on Hindi) with this model on
   synthetic speech: wrong script, wrong words, and a **latency that varies several-fold**. A
   wrong transcript flows through MT and render and reaches readers as confident text.
   `services/ai/speech/` (M3); `speech_indicconformer` exists as an alternative (#80) but cannot
   auto-detect a language. Synthetic Piper speech is not an elder's voice, so real recordings
   may be better or worse; **this says the risk is real, not how large it is.**
3. **English readers get text only**: render has no English voice (its README says so), so the
   `en` rendering is always `text_only`.
4. **Every reader receives every rendering of a message** (`MessageOut.renderings` is
   per-message); the client picks the one for its language.

### What this does NOT show
- One run per note, one CPU server, no concurrency or load. (Concurrency and load are in the
  next section.)
- Synthesized speech, not a person; and the stopgap that treats `webm_opus` as `ogg_opus` was
  **on** (OPEN_QUESTIONS #11), so the real browser path is only as good as that stopgap.
- Moderation is still the keyword stub.
- Translation quality was judged by me reading the Hindi and English output; the **Telugu
  text needs a native reader**.

## Ten notes at once (`run_proof_real.sh --load`)

Run 2026-10-06 on the same server: **16 CPUs, 32 GB, shared with three other stacks** (load
average 2.5 to 7.5 before each depth). **No CPU or memory limit was set, and that was checked
from inside every container**: `cpu.max` (cgroup v1 `quota=-1`), `Cpus_allowed_list=0-15`,
`NanoCpus=0`. So every number below is *unconstrained on a shared host*, not "a 4-core pilot
box". Real gateway, ASR, MT and render; moderation is the keyword stub;
`AI_ACCEPT_WEBM_AS_OGG_OPUS=true` (#2 is untouched); `JOB_LEASE_SECONDS=60`. The notes are the
same **5-second** Piper clips as above, **not 30 seconds**. 18 depth runs in four invocations, 85
notes (55 Telugu, 30 Hindi), one author, three readers (te, en, hi). Wall time is POST
`/messages` until the **last** reader could see the note. Raw files: `/tmp/aireal-load/` on the
server; the driver is `real_driver.py burst`, the tests for its arithmetic are
`test_load_stats.py` (CI does not collect them).

### What I predicted, and what happened

I predicted the first thing to break would be none of lease expiry, an AI-client timeout, memory
or the queue depth, but the **latency a user sees**, because the gateway runs **one worker loop
per process** and `process_message` jobs are taken one at a time, so ten notes are a queue of ten
and the AI services almost never see two requests at once. **Right**, and exactly so for Hindi:
`max running = 1` in all 18 depth runs, and ten Hindi notes finished at 6.5, 12.8, 19.1 … 61.8 s
(6.3 s each). **Wrong in one important way**: I assumed a note costs about 6 s. A **Telugu** note
costs **6 to 60 s** (below), so the queue is not a fixed multiple of a known cost.

### Numbers

| Depth, mix | p50 | p90 | worst | note |
|---|---|---|---|---|
| 1, Hindi | 6.3 s | | | 6.3 and 7.3 s in two runs |
| 1, Telugu | | | | **7.3, 7.4** (run 2); **30, 49** (run 3a); **48, 61** (run 3b): same code, same server |
| 3, Telugu (run 2) | 13.8 s | | 19.1 s | 7.5, 13.8, 19.1 |
| 10, Hindi | 31.6 s | 55.7 s | 61.8 s | a straight staircase |
| 10, Telugu, run 2 | 30.7 s | 54.9 s | 61.0 s | the fast Telugu run |
| 10, Telugu, run 3a | 190 s | 338 s | 389 s | each job 29 to 51 s |
| 10, Telugu, run 3b | 211 s | 441 s | 441 s | **9 of 10 delivered**: one was held (below) |
| 10, Hindi+Telugu | 96 to 119 s | 168 to 264 s | 199 to 270 s | three runs |

With ten samples "p90" is the ninth value, so read it as "the second-worst". **The wait is almost
all queueing:** at depth 10 the last note waits for nine others.

### Did the lease heartbeat hold under load?

Yes, for the case that was tested. On every job longer than the 20 s heartbeat interval the
lease was renewed (1 to 15 renewals per depth, seen as the remaining lease jumping back up for the
same job) and **never came closer to expiry than 40 s of 60 s**; no job needed a second attempt;
no log line about a lost lease or a failed heartbeat. **Not tested: contention between workers.**
There is one worker, so two gateway replicas claiming from the same table (OPEN_QUESTIONS #3)
were never exercised.

### Correctness (a single occurrence would have been a stop-and-report)
- **Delivered without a classifier verdict: 0. Duplicated (more than one classifier event, or a
  note a reader saw twice): 0. Lost: 0.** Counted in the database and from each reader's view.
- **Two Telugu notes were held, not lost**: the ASR returned a garbage transcript (Arabic and
  Sinhala characters), the translation came back empty, and the pipeline held the note for a
  human (`system` `HOLD`, no notice text). Nobody was shown anything: the readers never see it,
  and **the sender gets no signal** (#18). 2 of 55 Telugu notes; 0 of 30 Hindi.
- **No rendering audio was silence**: every audio rendering fetched as a reader was measured
  non-silent. One Hindi rendering (built from a garbage Telugu transcript) lost its audio: the
  render service raised `wave.Error: # channels not specified` and the rendering degraded to
  `tts_skipped` (text only). Handled gracefully; the cause is in `services/ai/render` (M3's).

### The host
- **CPU:** the host was never above about 70% busy (30 to 48% idle at the worst second; this
  includes the other tenants). Per service, from the process tree: **speech 2.1 to 3.5 cores
  on average while transcribing** (its 4 ASR threads), mt and render burst to **8 to 9 cores for a
  second or two** per call. CPU is a lower bound (whole seconds; a child that exits between samples is
  missed).
- **Memory:** peaks speech 1.5 GB, mt 1.5 GB, render 2.0 to 2.7 GB, gateway 0.12 GB, postgres
  0.14 GB (about 6 GB for the stack); `MemAvailable` never below 25 GB. **Swap** was a constant 177 MiB
  (not ours) with under 16 KiB/s in or out. No `OOMKilled`, no restarts. Over about 85 notes render's
  memory went up and down (2.5, 2.7, 2.5, 2.7 GB): not a leak test.

### Where the first run was wrong (and how it was found)
My first run printed identical CPU and memory for every container, and host figures that contradicted each
other ("95% idle" next to "60% busy"). Causes: on this host the containers share one cgroup, so
`docker stats` and the cgroup files report the same numbers for all of them, and `docker top`
fails in runc; and `vmstat` reprints its header mid-file, which my awk compared as a number. Both are
fixed and the host figures above were **recomputed from the raw files**. The per-service sampler
was checked against the live stack (distinct values) before it was used again. A first version of the
audit also counted earlier notes in the shared circle as "unexpected".

### What this does NOT show
- **A 30-second note.** M3's p90 is for a 30 s note; these are 5 s. Telugu took 6 to 60 s for
  5 s of audio, and `AI_TRANSCRIBE_TIMEOUT_S` is **120 s**. If the cost grows with length, a 30 s
  Telugu note could hit the timeout (a retryable error: it would be retried with backoff). Not
  measured.
- **Why the same Telugu note took 7 s in one run and 30 to 60 s in another.** Piper re-synthesizes the
  sample every run and run 2's file was not kept (hashes are printed from run 3 on); within one run,
  identical audio gave 41 different transcripts across 55 Telugu notes, which looks like Whisper's
  temperature fallback (slow, random) but is **not proven**. Speech is M3's.
- A smaller host, a CPU limit, real traffic (one author sending ten notes in the same
  second is not how elders use it), the real moderation classifier, or two gateway replicas.

## Still not decided (M3's / yours)

Which ASR is the real one (`speech` vs `speech_indicconformer` — the latter is **not**
in this file; it cannot auto-detect a language, and an undeclared voice note would be
rejected and held), how `webm_opus` reaches the AI services, and whether a shared volume
is the permanent way they read audio (`OPEN_QUESTIONS.md` #2, #11). Real JWT auth, which
must land before the moderator routes are reachable by anyone real, is a prerequisite for
switching the pipeline on for real users (`GET/PATCH /me/settings`, the other one, is
merged).

## Re-running the proof

```bash
cp ~/veerendra/.env .env        # POSTGRES_* and GATEWAY_* only; never commit it
infra/ai/e2e/run_proof.sh       # mock MT/render: ~10 min first run (model download), then ~5 min

# the real MT and render: .env must also have HF_TOKEN, from an account that has accepted
# the gate on BOTH ai4bharat/indictrans2-*-dist-200M repos. First run downloads the models.
infra/ai/e2e/run_proof_real.sh

# ten notes at once instead of the two single notes: a depth is N, or N:hi / N:te / N:hi+te
infra/ai/e2e/run_proof_real.sh --load "1:te 1:te 10:hi 10:te" --keep
```
