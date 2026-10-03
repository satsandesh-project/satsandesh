# Open questions — services/gateway/

Things this week's media-store/job-queue/retention work either guessed at
or deliberately deferred, because they depend on a decision this repo
hasn't actually made yet, not on anything left to build. Mirrors the
format of `contracts/chat/OPEN_QUESTIONS.md` and
`services/ai/OPEN_QUESTIONS.md`.

1. **The format/transcode decision has never actually been made — only
   reserved.** `contracts/chat/DECISIONS.md` #14/#15 leave room for "a
   backend may transcode before persisting," but
   `app/media_storage.py`/`app/media.py` do not transcode anything today
   — whatever format a client uploads (`webm_opus`, `ogg_opus`,
   `wav_pcm16`, `mp3`) is exactly what gets stored and served back. Nobody
   has decided whether the pilot needs a single canonical storage format
   (smaller, more uniform, but a real transcode step and dependency) or
   whether "store whatever arrived" is fine at this scale. This matters
   now specifically because of open question #2 below — M3/M4 own that
   call, not M2.

2. **`contracts/ai/common.py`'s `AudioFormat` doesn't cover every format
   `contracts/chat/`'s media contract allows.** `webm_opus` is a valid
   upload format (`app/db/models.py::MediaObject`'s own CHECK constraint)
   but has no matching value in `contracts/ai/common.py`'s `AudioFormat`
   enum — found while wiring `app/jobs.py`'s `transcribe_media` handler
   (Step 2). A `webm_opus` upload's transcription job fails loudly right
   now (`ValueError` → retried → eventually dead-lettered) rather than
   silently mislabeling the format. Not fixed here: `contracts/ai/` is
   out of scope this week, and the actual fix (add the missing enum
   value, or pick a real transcode step per #1) is a cross-package,
   cross-lane decision.

3. **What the job queue does NOT do.** Worth being explicit, since none
   of this is visible from the code alone:
   - **Not multi-process.** One worker loop per gateway process
     (`app/jobs.py::run_worker_loop`), started from that process's own
     `app/main.py` lifespan. Running two gateway replicas gives two
     independent workers polling the same table, which is safe (the
     claiming `UPDATE` is race-safe — see `app/db/models.py`'s `Job`
     docstring) but uncoordinated; there's no shared view of "how many
     workers are currently running."
   - **Not scheduled/recurring.** `claim_next_job` only ever picks up a
     row that already exists with `status='queued'`. Nothing re-enqueues
     a job automatically on a timer — that's why `app/retention.py`'s
     sweep is its **own** separate loop with its own timer
     (`MEDIA_RETENTION_SWEEP_INTERVAL_SECONDS`), not a recurring queue
     job. If a future feature needs "run X every N minutes," it needs the
     same kind of dedicated loop, not this queue.
   - **Not priority-ordered.** `claim_next_job` orders strictly by
     `next_attempt_at`, then `created_at` — first due, first claimed.
     There's no way to say "this job type matters more."
   - **Lease renewal and process identity -- fixed (hardening pass).**
     These were open limits when the queue first merged and are closed
     now: a heartbeat renews a running job's lease every lease/3 seconds
     (`app/db/repository.py::extend_job_lease`, driven from
     `app/jobs.py`), so a job longer than `JOB_LEASE_SECONDS` is no
     longer handed to a second worker mid-flight; `complete_job` /
     `fail_job` / `extend_job_lease` only act for the worker that still
     holds the job, so a slow worker's late result can't overwrite the
     worker that reclaimed it; and `worker_id()` is
     `gateway-<pid>-<random 8 hex>`, unique per process incarnation (the
     bare pid was `1` in every fresh container). Still true: the lease
     should be a comfortable multiple of the heartbeat interval, and a
     process that is suspended for a whole lease still loses its job
     (correctly -- the other worker's result stands).
   - **Permanent vs. transient failures.** A handler can raise
     `PermanentJobError` to land a job in `dead` after one attempt
     (no format mapping, a 4xx from the AI service); anything else is
     retried with backoff up to `JOB_MAX_ATTEMPTS`, which is now actually
     honored when a job is enqueued.

4. **Disk estimate for the 30-elder pilot — and where it's grounded, not
   guessed.** ~100KB per 30-second Opus voice note is itself an
   *estimate*, not a measurement — nobody has real elder-recorded audio
   to size yet. On that basis, with the new 30-day retention actually
   enforced (`MEDIA_RETENTION_DAYS`, `app/retention.py`), storage should
   plateau rather than grow unbounded, at roughly:
   - Light use (3 notes/elder/day): ~880MB/day → ~26GB steady-state
   - Heavy use (15 notes/elder/day): ~4.4GB/day → ~130GB steady-state

   Checked directly against the shared server (10.110.11.31,
   2026-09-23): `df -h /` shows 703GB free out of 1.8TB, with 1008GB
   already used. That headroom is **shared** across at least four
   teammates' own deployments/experiments on the same box (`~satsandesh`
   has `guna_sai/`, `kshitiz/`, `Sainathan_V/`, and `veerendra/`
   subdirectories), not reserved for media — so "even the heavy case
   fits" is true in isolation but doesn't account for what else is
   growing on this host. Worth someone measuring real note sizes and
   real per-elder usage once the pilot actually starts, rather than
   trusting either estimate above.

5. **`app/undo.py` is still the in-memory `dict[str, asyncio.Task]`
   pattern** this week's job queue was explicitly built to demonstrate
   the alternative to -- see that file's own docstring. What changed in
   the hardening pass: a restart no longer strands a message. At startup
   `app/recovery.py::recover_pending_fan_outs` re-schedules delivery for
   every message still `pending` (immediately if its undo window already
   elapsed, otherwise after only the time left); it is safe to run twice
   or from two processes because `fan_out_message` only acts on a
   `pending` message through an atomic status transition. What is still
   true: `app/undo.py` itself is unchanged and per-process (an undo
   request that lands on a different gateway process than the one that
   scheduled the delivery can't cancel that process's task -- the DB
   status check still prevents a double or post-undo delivery, but the
   task keeps sleeping), and moving the undo window onto the `jobs` table
   so it needs no recovery at all would mean rewriting that module and
   how `DELETE /messages/{id}` cancels -- a bigger change to code another
   member wrote, left as the proper follow-up.

6. **Swept media and message history don't currently interact, because
   nothing does yet.** `messages.original_media_ref` is still a plain
   text URI column, unrelated to `media_objects` (`app/db/models.py`'s
   `MediaObject` docstring says so directly — this hasn't changed this
   week). So today, sweeping a `media_objects` row has no visible effect
   on message history at all; the only real behavior change is that
   `GET /media/{id}` starts 404ing for that id. Once a message's
   `media_ref` really does point at a `media_objects` row (later work),
   whoever wires that up will need to decide what a message referencing
   swept audio should look like in the message list itself, not just at
   the fetch endpoint — this week's sweeper doesn't touch that, because
   the connection it would need doesn't exist yet.

7. **The transcript `transcribe_media` produces isn't persisted
   anywhere, so the job is now opt-in (`TRANSCRIBE_ON_UPLOAD_ENABLED`,
   default off).** It's logged (`app/jobs.py::_handle_transcribe_media`) and
   nothing else — there's no column or table for a transcript today.
   Storing it, and deciding what "the transcript" even means once the
   real orchestrator exists (`denoise -> transcribe -> pivot -> moderate
   -> render`, `docs/retro/month-1.md`'s Week 7 row), is that Week 7
   design's job, not something this week should pre-empt by inventing
   schema for it now.

8. **`AI_SERVICE_URL` defaults to `http://ai-services:8001`** — the
   hostname `docker-compose.yml`'s existing `ai-services` service already
   uses, on the assumption that whatever eventually runs there (the real
   mock, or the real service) will keep that name. Today that service is
   still the Week-1 health-check-only skeleton at the repo root
   (`ai-services/main.py`), not `services/ai/mock/app.py` — the Step 2
   proof ran `services/ai/mock/app.py` manually in its own container
   rather than through `docker-compose.yml`, specifically to avoid
   permanently rewiring shared infra for a proof. Whether
   `services/ai/mock/` should replace or run alongside the root
   `ai-services/` skeleton in `docker-compose.yml` is a call for whoever
   owns that integration (Week 7's orchestrator work), not decided here.

9. **`find_expired_media` sweeps by age alone — no moderation-status
   awareness — and that needs to be on record before the sweeper runs on
   staging.** `app/db/repository.py::find_expired_media` selects every
   `MediaObject` older than `MEDIA_RETENTION_DAYS` and `delete_media_object`
   hard-deletes it; today that's fine, because no moderation state exists
   to protect. But #65 (a HOLD/BLOCK appeal flow, also a ~30-day window)
   will change that: once it lands, this sweeper as written could delete
   the exact evidence an appeal needs, potentially while that appeal is
   still open. Flagged by @sainathanv in review of this PR — not fixed
   here, since the `moderation_events` table (#65) doesn't exist yet and
   there's nothing to filter on. Three ways this could go once #65 lands,
   in the order he ranked them: (a) exempt any media object with an
   open/unresolved moderation status from the sweep; (b) tombstone instead
   of hard-delete, so a swept id fails closed with a distinguishable reason
   instead of a plain 404 indistinguishable from "never existed" (see #6
   above); or (c) explicitly decide the window overlap is acceptable and
   the two systems don't need to coordinate. @sainathanv said he'll own
   resolving this when he builds the moderation console — this item exists
   so the collision is documented before the sweeper is live on staging,
   not to pre-empt that design.

10. **Who may download a voice note, beyond the author and delivered
    recipients.** `GET /media/{id}` now allows only the author, or a
    recipient of a message carrying the media whose status is `sent` or
    `delivered` and that isn't deleted (a DM's target user, or a circle's
    members); everyone else gets the same 404 as a missing id. That is
    deliberately the narrowest rule that lets the app work today. Week 7's
    moderator console will need moderators/admins to hear a `held` message
    they are reviewing -- a role-based allowance to add when that lands,
    not before: until then a held message's audio is unreachable by
    anyone but its author.
