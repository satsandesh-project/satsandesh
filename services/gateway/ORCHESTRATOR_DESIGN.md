# The pipeline orchestrator — design note (Week 7, Phase 5)

Status: proposal, written before the code. The decisions in **bold** are the
ones a reviewer should challenge; each says what it assumes and what it costs.

## What it has to do

A message is written once, in the sender's language. Before anyone but the
sender can see it, the gateway must: transcribe it (voice), translate it to an
English pivot, have the moderation classifier rule on the pivot, and render it
into each receiver's language (text + speech). `docs/retro/month-1.md`'s Week 7
deliverable: *a note becomes N renderings*.

## The problem the first draft would have had

`app/messages.py::fan_out_message` runs when the 30-second undo window ends and
flips `pending → sent`. **It does not look at the pipeline.** CPU ASR + MT +
TTS on a shared server can easily outlast 30 s. A pipeline built as "just add a
job" would then deliver the message with no renderings — and, because Phase 4
makes renderings *fixed at delivery*, they could never arrive; a held message
could even be delivered before its moderation verdict. So the orchestrator is
mostly about **gating delivery**, not about calling four services.

## Decisions

### 1. Delivery is gated by `messages.pipeline_state`, set in the same transaction as the message

`pipeline_state` is `NULL` (no pipeline — the default, and every existing row),
`'pending'`, `'complete'` or `'failed'`. When `PIPELINE_ENABLED` is on, creating
a message sets `'pending'` **and enqueues the `process_message` job in the same
transaction** — so there is no window in which a message exists without its
job, and a gateway restart loses nothing (the job table is durable).

`fan_out_message` gains one line: if `pipeline_state == 'pending'`, return
without delivering. `app/undo.py` is **not touched** — its in-memory registry
stays as it is (OPEN_QUESTIONS #5), which is the point of putting the gate in
the delivery function rather than in the scheduler.

*Why a column and not "is there an open job?":* a job's status flips after its
handler returns, which leaves a gap in which the gate is still closed but
nobody will open it. A column the handler sets itself, before looking at the
clock, has no such gap (next point).

### 2. Who delivers, and why no wake-up can be lost

Either the scheduled fan-out or the job delivers, and the order of the
handler's last steps makes sure one of them always does:

1. handler stores every stage's output (each stage committed),
2. handler applies the moderation outcome (hold / block flips the status),
3. handler sets `pipeline_state = 'complete'` and **commits**,
4. *then* it reads the clock: if the undo window has already elapsed, it asks
   the event loop to run `fan_out_message` now.

If the scheduled fan-out ran **after** step 3 it sees `'complete'` and
delivers. If it ran **before** step 3 it saw `'pending'` and skipped — which
means the window had already elapsed, so step 4 sees that and delivers.
`fan_out_message`'s own atomic `pending → sent` transition makes a double
trigger harmless. A restart is covered too: `app/recovery.py` re-schedules
every pending message (the gate keeps the unfinished ones waiting) and the
durable job resumes.

### 3. Stages are resumable and every write is idempotent

Each stage's output is stored (Phase 3/4) and the stage is skipped if its
output already exists: transcript on the message, `pivot_text_en`, a
classifier event, a rendering per language. A job retried after a crash or a
timeout picks up where it stopped and never double-writes. Writes are guarded
to a `pending` message in the SQL itself, so a message undone mid-pipeline is
simply abandoned.

### 4. **Moderation failures fail closed; failures after an ALLOW degrade**

- ASR or MT unavailable after retries, an empty transcript (a silent note), an
  unsupported language, or moderation unreachable → the message is **held** for
  a human, with a `SYSTEM` event saying why. We cannot verify it, so we do not
  send it. (The classifier itself already fails closed: a timeout is a 200 with
  `HOLD`, `degraded`, which is stored as the classifier's own event.)
- Render unavailable after retries, **once moderation has said ALLOW/NUDGE** →
  the message is delivered with whatever renderings exist (possibly none; the
  receiver reads the original). Translations are an enhancement; moderation is
  the gate. This is the "graceful degradation" the Week 8 row asks for, decided
  here so the failure behaviour is not accidental.

*Cost:* until M3 decides how WebM/Opus reaches the AI services (OPEN_QUESTIONS
#2), a real browser voice note cannot be transcribed, so with the pipeline on
**every real voice note is held**. That is the honest consequence of fail-closed;
`AI_ACCEPT_WEBM_AS_OGG_OPUS` is the stopgap, off by default.

### 5. **Actions → status** (the mapping the chat contract left open)

`ALLOW → deliver`. `HOLD → held`. `BLOCK → blocked`. **`NUDGE → deliver`, with
the nudge recorded** (`PIPELINE_NUDGE_DELIVERS`, default true; set false to hold
instead). This is an assumption about M4's policy — the chat contract says the
mapping is "the gateway's, not either enum's" and leaves it open. `HOLD` and
`BLOCK` still get their renderings produced (stored, but invisible until the
message is out — Phase 4's visibility rule), so that a later moderator
**release** needs no re-run: it just flips the status.

### 6. Languages: recipients' preferred languages only (M1's answer on #83)

Target languages = the distinct `preferred_language` of the recipients (the DM
target, or the circle's members except the author), restricted to the three
supported languages, **minus the message's own language** (a receiver in the
sender's language reads the original). A language the pipeline cannot render
(e.g. `ta`) is skipped, not an error.

### 7. Rendering audio is read from a shared directory and ingested

The render service returns a `file://` path on **its own** disk. The gateway
reads the file by **file name only** from `AI_RENDER_AUDIO_ROOT` (so the two
containers may mount the directory at different paths), stores it as a media
object owned by the message's author, and the rendering points at it. If the
file cannot be read the rendering is kept as text with `tts_skipped` — a missing
voice never costs the text.

### 8. The sender's notice

For a non-`ALLOW` verdict the classifier's notice is an English master (the
mock's is Telugu). The orchestrator asks the render service to translate it to
the sender's language and records what it produced in the event's
`notice_text`; if that fails the event records no notice (`null`), honestly,
rather than the English text as if it had been told. **There is no wire surface
yet for showing a classifier-hold notice to the sender** — the `message.status`
frame carries only the status. That is a contract gap for M1/M4, listed in
OPEN_QUESTIONS, not something this phase invents.

## Off by default

`PIPELINE_ENABLED=false`: nothing in this note changes any existing behaviour,
on staging or anywhere. Turning it on needs the real services reachable
(Phase 7) or `services/ai/mock/`.

## Not in this phase

The moderator console routes (Phase 6), wiring the real AI services into
compose (Phase 7), real JWT auth, and moving `app/undo.py` onto the jobs table.
