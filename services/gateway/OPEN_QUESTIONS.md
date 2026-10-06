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
   (Step 2). A `webm_opus` upload's transcription job fails loudly
   (`PermanentJobError` → dead after one attempt) rather than silently
   mislabeling the format. Not fixed here: `contracts/ai/` is out of
   scope, and the actual fix (add the missing enum value, or pick a real
   transcode step per #1) is a cross-package, cross-lane decision (M3's).
   **Week 7 stopgap, off by default:** `AI_ACCEPT_WEBM_AS_OGG_OPUS=true`
   labels WebM/Opus as `ogg_opus` in the AI request
   (`app/ai_audio.py`). ffmpeg sniffs the container from the bytes, and a
   real WebM/Opus file decoded with the ASR service's exact command (host
   ffmpeg 4.2.7; not Chrome `MediaRecorder` output, and the service itself
   was not run), so it should work — but it is a mislabel in the request,
   which is why it is an explicit opt-in and not the default.

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

7. **The `transcribe_media` upload job still only logs its transcript —
   but the orchestrator now has somewhere to put one.** Week 7 Phase 4
   added `messages.transcript` / `transcript_language`, `pivot_text_en`
   writers and the `message_renderings` table (`app/db/renderings.py`); the
   Week 6 `transcribe_media` job (opt-in, `TRANSCRIBE_ON_UPLOAD_ENABLED`) is
   untouched and still logs. It is keyed to a media object, not a message,
   so it cannot store a transcript on one; the Week 7 orchestrator job, which
   runs per message, supersedes it.

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

   **DECIDED (Week 8): option (a), for `held` only.** `moderation_events` and the moderator console
   exist now, so the collision is real, not hypothetical. `find_expired_media` skips any media
   carried by a `held`, not-deleted message, as the voice note itself or as a rendering's audio,
   however old it is; protection ends when the message leaves `held` (tests:
   `tests/test_retention_held.py`, with a sabotage check). Consequences and what is NOT decided:
   1. **`blocked` is not protected.** A block is a ruling; whether blocked audio should outlive 30
      days for an appeal (appeals are Week 9, #65) is a retention and privacy policy decision for
      people, not for a query. One line (`_UNRESOLVED_STATUSES` in `app/db/repository.py`).
   2. **Held audio is kept until a human rules: there is no upper bound.** A held message nobody
      opens keeps an elder's voice indefinitely. With the stuck-pipeline watchdog (#20) now
      sending stuck messages to the same queue, that is more likely, not less. A maximum (say 90
      days, then ...what?) is a policy call.
   3. **The clock is the media's own age, not time since release.** A message held for more than
      30 days and then released has audio that is eligible for the very next sweep: its recipients
      may lose the audio within the hour (the text stays). The test
      `test_once_a_held_message_is_released_its_old_audio_is_swept_again` pins this as the current
      behaviour; if a released message's audio should get a fresh 30 days, that needs a clock that
      restarts on release (a column or the event's timestamp), and a decision to do it.
   4. Option (b), a tombstone so a swept id is distinguishable from a missing one, is still not
      built (#6).

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

11. **The real ASR cannot open `media:<id>` — how the AI services read
    audio is undecided.** Found in Week 7 by reading `services/ai/`:
    both ASR services resolve `AudioRef.uri` as a local path or `file://`
    URI (`_resolve_local_path`), and render returns a `file://` path on its
    own disk. The gateway's Week 6 transcribe handler sent `media:<id>`,
    which the mock accepts (it never opens the file) and the real service
    would reject with 422 for every note. `app/ai_audio.py` now builds a
    `file://` URI when `AI_AUDIO_MOUNT_ROOT` is set (the ASR container mounts
    the media volume there) and keeps `media:<id>` when it is not. That is
    one of two resolutions; the other is a contract change (the AI services
    fetch bytes over HTTP, or `AudioRef` carries a fetchable URL) — M3's
    call. **Provisional:** the shared-volume path works on one host and
    would not survive the AI services moving to another machine or to
    object storage.

12. **The mock and the real AI services disagree in ways a client of both
    must survive.** (a) The mock injects every `ErrorCode` as a 422,
    including the transient ones (`TIMEOUT`, `OUT_OF_MEMORY`), where a real
    service answers 5xx/503 — `app/ai_client.py` therefore classifies by
    error code as well as status. (b) The mock's NUDGE notice is Telugu
    (`nudge_language: te`); the real moderation service's notices are
    always English masters (`nudge_language: en`), so nothing may assume the
    notice is already in the sender's language. (c) No real service
    registers an exception handler: a schema-invalid body is FastAPI's
    default `{"detail": [...]}` 422 (not a `PipelineError`), and an
    unexpected exception is a bare 500 — both handled, both worth M3/M4
    knowing about. Not fixed here: `services/ai/` is out of scope.

13. **`moderation_events` is append-only by trigger, not by privilege.**
    `docs/security-checklist.md` B5 asks for the application's DB role to hold
    INSERT/SELECT on this table and nothing else. Today the app connects as
    the database owner (`POSTGRES_USER`), so a GRANT/REVOKE would not bind it,
    and the owner can drop the trigger. The trigger (`c696e9f74472`) stops
    application bugs and accidental statements and is tested; it is not a
    defence against someone with the owner's credentials. The real fix is a
    separate low-privilege app role -- an infra change (`db/init/`, compose
    env), not done here. Also: the one purge path
    (`SET LOCAL app.allow_audit_purge = 'on'`) is for erasure on request;
    nothing calls it yet outside test cleanup, and who may invoke it is
    undecided.

14. **Who may open a held message's audio is a global-role rule only.**
    `user_can_fetch_media` lets `users.role` in (`moderator`, `admin`) fetch
    the audio of a `held` or `blocked` message. A circle-level moderator
    (`memberships.role`) is not covered, and neither is a moderator
    re-reviewing a message that was already `sent`. Matches the console as
    M4's contract describes it (one global queue); say so if circle
    moderators are meant to review their own circle. #9 (retention sweeps a
    held message's audio at 30 days) is still open and is now the more
    pressing of the two: a held message can outlive its audio.

15. **Rendering audio has no producer yet, and its owner/retention are
    choices worth confirming.** `message_renderings.audio_media_object_id`
    points at a `media_objects` row; nothing creates those rows yet. The
    render service returns a `file://` path on its own disk, so the
    orchestrator (Phase 5/7) must ingest those bytes into the media store.
    Assumed: the row is owned by the **message's author** (`author_id` is
    NOT NULL and gates the author's own access), and it is swept by the
    same 30-day retention as any media object -- after which the rendering
    keeps its text and loses its audio (`FK ... SET NULL`, tested). A
    translated note's audio vanishing at 30 days while its message stays is
    consistent with the original's, but say if renderings should be kept
    longer or re-synthesized on demand. Also: only a message's *original*
    audio is open to a moderator (#14), not its renderings'.

    **UPDATE (Week 8):** the producer exists (the pipeline ingests render audio, owned by the message's
    author, as assumed above) and the sweep treats it like any media with one exception: a rendering's
    audio is kept while its message is `held` (#9). Everything else here is unchanged and still a
    choice for people: renderings follow the same 30-day window as the original, and (#9, point 3) a
    long-held message that is then released can lose both within an hour.

16. **Transcript and renderings are hidden until a message is `sent`, even
    from its author.** Stored while `pending` (the pipeline runs inside the
    undo window) but exposed on the wire only once the message is out
    (`message_to_out`), so a held or pending message leaks neither a
    translation nor a transcript. The sender therefore sees no transcript of
    their own voice note until it is delivered. If the sender should be able
    to read the transcript while pending (to check ASR before the undo window
    closes), that is a UX call for M1 and a one-line change here.

17. **A classifier NUDGE delivers — an assumption about M4's policy.** The
    chat contract leaves the action -> status mapping open ("the gateway's,
    not either enum's"). The orchestrator maps ALLOW and NUDGE to delivery,
    HOLD to `held`, BLOCK to `blocked`; `PIPELINE_NUDGE_DELIVERS=false` holds
    nudged messages instead. Say so if a NUDGE should mean something else
    (hold until the sender acknowledges, say) -- the setting is the whole
    change.

18. **The sender's notice has no wire surface.** For a non-ALLOW verdict the
    orchestrator translates the classifier's notice into the sender's language
    and records it in the event's `notice_text`, but the only thing the sender's
    client receives is a `message.status` frame carrying `held`/`blocked`. So a
    sender whose message was held sees the status change and not why. That is
    a contract gap for M1/M4 (a notice field on `MessageStatusOut`, or an
    endpoint), not something the gateway should invent. The proposal's promise
    that nothing happens silently is, today, honoured in the audit trail and
    not yet on the sender's screen.

19. **With the pipeline on, every real browser voice note is held until the
    `webm_opus` decision (#2).** Fail-closed, deliberately: a note that cannot
    be transcribed cannot be ruled on, so it goes to a human. The stopgap
    `AI_ACCEPT_WEBM_AS_OGG_OPUS=true` makes them flow. Worth knowing before
    switching the pipeline on for real users: with the default it would send
    every voice note to the moderators' queue.

20. **Nothing detects a message stuck behind a dead pipeline.** The dead-letter
    hook holds (or releases) the message when its job dies, and recovery
    re-schedules fan-outs at startup, but a `pipeline_state = 'pending'`
    message whose job vanished (deleted by hand) or whose hook itself failed
    (logged, never raised) waits forever -- and the gate keeps it from
    delivering. A watchdog that holds messages stuck `pending` past a deadline
    is the missing safety net; not built here (it needs a deadline someone has
    to choose).

21. **Closed by the gate: the unlocked pending check on rendering writes.** The
    #87 review noted `upsert_rendering`'s pending check does not lock the row,
    so a write racing the `pending -> sent` flip could land after delivery.
    With the orchestrator, delivery cannot start until the pipeline has
    finished writing (`pipeline_state`), so the race has no writer left in the
    pipeline's own flow. It remains possible only for a writer outside the
    pipeline.

22. **Releasing a held message (Phase 6) must also deliver it.** The renderings
    of a held message already exist (stored, hidden until out), so a release is
    a status flip -- but a flip alone sends nothing: `fan_out_message` is what
    broadcasts `message.new`. The release route has to trigger delivery the same
    way the orchestrator does.

23. **The moderator routes are only as trustworthy as the identity behind
    them — and identity is still a stub.** `app/auth.py` accepts any UUID as a
    bearer token with no signature or expiry, so anyone who knows (or can
    guess) a moderator's user id can act as them: read the queue, hear held
    audio, release or block. The routes check the database role correctly
    (`users.role`; the token-derived role is always `elder`), but that is a
    lock on a door with no wall. Real JWT verification must land before any
    moderator account exists on a deployment real people can reach. Not in
    scope for this phase; recorded because it is the biggest risk the
    console adds.
    **UPDATE (Week 8, step 1 of 2):** signed tokens now exist (`app/tokens.py`, HS256,
    `sub`/`iss`/`iat`/`exp` all required, algorithm pinned) and `AUTH_MODE=jwt` accepts only
    them; a token that looks like a JWT is verified strictly in EVERY mode, and the role is
    read from `users.role` on each request. **The risk above is NOT closed:** the default is
    still `AUTH_MODE=legacy` (a UUID is that user), because the elder app cannot obtain a
    signed token yet (#32), and no deployment runs `jwt`. It closes for a deployment when
    that deployment runs `jwt`. Until then `/moderation*` stays unrouted by Caddy.

24. **Console scope decisions worth confirming with M4.** (a) The queue shows
    only `held` messages; `blocked` ones are not browsable (appeals are
    Week 9), though a block can be reversed by releasing it if the id is
    known. (b) A moderator is a global role (`users.role`); a circle's own
    moderators (`memberships.role`) cannot review their circle. (c)
    `ModerationQueueItem.original_text` is `null` for a voice note per M4's
    contract; whether it should carry the transcript is M4's call (asked on
    #86). (d) `contracts/chat/mock/` has no moderation routes, so the console
    has no mock to build against; adding them is a small `contracts/chat/`
    change not made here. (e) `notice_sent` is always `false` (see #18).

25. **Found Week 7 (after Phases 4-6 merged): the read path leaked every
    message that was not out.** `get_messages_since` returned every
    non-deleted message in the conversation whatever its status and whoever
    asked, so a recipient who synced (HTTP or WebSocket) read -- text and
    `media_ref` included -- a message still inside its undo window, one a
    moderator had held or blocked, one the sender had cancelled. Moderation
    and undo were protected only by the fan-out, not by the read path. Fixed
    by `get_visible_messages_since` (the author sees their own in every
    status; everyone else only `sent`/`delivered`; filtered before the page
    limit). **My own Phase 4-6 write-ups overstated what they protected**:
    "hidden until out" covered renderings and the transcript, not the
    original text, and no test read a held message as its recipient. Still
    true and still open: (a) the contract README documents the HTTP sync
    cursor as `?since=` while the gateway route's parameter is `since_id` (a
    client using the documented name silently gets the first page again); (b)
    a message's *author* still reads their own held/blocked text, which is
    intended (their screen needs it) but means "held" is hidden from
    recipients, not from the sender.

26. **The real ASR hallucinates words from non-speech, so "no speech -> hold" never
    fires.** Found by the end-to-end proof (`infra/ai/README.md`): faster-whisper `small`
    transcribed a 2 s sine tone as **"Beep"** and 3 s of digital silence as **"You"**. The
    orchestrator's rule (an empty transcript is held for a person, never delivered
    unclassified) is correct but is never triggered by this ASR; a silent or noisy note is
    delivered with a junk transcript, and moderation rules on a meaningless pivot. The
    place to fix it is `services/ai/speech/` (M3: VAD / no-speech filtering in the engine),
    not the gateway; recorded here because the gateway's safety story leans on it.
    **M3's decision (#98, 2026-10-05):** a VAD / no-speech filter goes in the same change as
    denoise, order denoise -> VAD -> ASR; today `engine.py` calls `transcribe()` with no
    `vad_filter`, which explains "Beep" and "You". Nothing changes in the gateway: an empty
    transcript is already held for a person (`test_a_silent_note_is_held_for_a_human_...`),
    and `contracts/ai`'s `TranscribeResponse.text` has no minimum length, so empty is a valid
    answer. One detail for the service: `detected_language` is still REQUIRED, so an empty
    result must still carry some language. Still open until it ships.

27. **Releasing a pipeline-failure hold does not re-run the pipeline.** In the proof, a
    message whose pipeline died at the moderation stage (service down) was held with a
    SYSTEM event; once a moderator released it, the recipient received it with its
    transcript but **no renderings**, because the render stage never ran and a release is
    a status flip. Options: a release re-enqueues the missing stages (writes are guarded
    to `pending`, so this needs a deliberate "reopen" path), or accept original-only for
    that case. Not decided.

28. **Quiet hours are saved but never enforced until the client sends a timezone.**
    `GET/PATCH /me/settings` now exists (`app/users.py`) and the elder app's existing
    PATCH (start, end, language, tts) works against it unchanged. But `is_quiet_hours`
    returns False for a user with no `timezone` (it will not assume UTC), and the client
    sends none -- so a window an elder carefully sets has no effect on push. The fix is one
    line on the client (M1): send `Intl.DateTimeFormat().resolvedOptions().timeZone` with the
    first PATCH. Not done here (`clients/elder-app/` is M1's). A server-side fallback (guess a
    zone from the request) was rejected: a wrong guess suppresses or sends a push at the
    wrong hour with no error anywhere.

29. **What still blocks switching the pipeline on for real users** (after this change,
    `/me/settings` no longer does): real JWT verification **turned on** (the code exists since
    Week 8 and is verified, `AUTH_MODE=jwt`, but it is off by default and no deployment runs it:
    the moderator routes and held-audio access still rest on a stub identity until one does,
    #23, and switching it on needs the elder app to obtain a signed token first, #32); M1's `timezone` on `/me/settings` (#28; #92, which
    sends `source_lang` so typed messages are not all treated as Telugu, has since merged);
    M4's answers (#24) and a notice surface
    for a held sender (#18); and M3's ASR choice, `webm_opus` handling and the
    hallucinated-transcript problem (#2, #11, #26). Also: Caddy now routes `/me/settings` and
    `/audio-labels/*` to the gateway, but still not `/onboarding*` or `/push*` (the elder app
    does not call them -- the first client to implement push or QR onboarding needs those
    routes), and deliberately not `/moderation*`.

30. **The Week 7 task names a "denoise" stage that does not exist.** The plan
    (`docs/retro/month-1.md`, "Week 7 -- third layer") gives M2 "denoise -> transcribe ->
    pivot -> moderate -> render per receiver". The orchestrator (`app/pipeline.py`) runs
    transcribe -> pivot -> moderate -> render. There is no denoise contract in `contracts/ai/`
    and no denoise service or engine in `services/ai/`, so there was nothing to call -- and I
    did not record that when I built the orchestrator, which is how it went unnoticed until a
    completeness check against the plan. Whether it belongs inside the speech service (no
    gateway change), as its own stage with a contract (I would add an optional stage that
    degrades to the original audio), or not in Month 2 is M3's call: asked on #98. Not
    measured: whether denoising would help real noisy recordings at all. Related, and possibly
    the same fix: the ASR hallucinating words from silence and tones (#26).

    **RESOLVED (2026-10-05, M3 on #98): denoise lives in `services/ai/speech/`, before ASR.**
    No gateway change and no new contract; the orchestrator keeps sending the same request.
    It will be RNNoise (`pyrnnoise`), behind a switch that ships OFF until M3 has A/B'd it on
    real noisy Telugu recordings (nobody has shown denoising helps Whisper; it can hurt). It
    covers the faster-whisper service only; M3 will test it in front of
    `speech_indicconformer` too and add it there only if it helps. So the Week 7 line's
    "denoise" stage exists as a service-side step that is currently off, not as an
    orchestrator stage.

31. **The real ASR is not adequate for Telugu on current evidence, and the fix is not
    decided.** The real-services proof (`infra/ai/README.md`) gave the same synthetic Telugu
    sentence three different transcripts (two scripts, one in Devanagari), and the wrong
    meaning reached readers with no signal; latency ranged ~6-37 s (unexplained). M3
    (#98): `faster-whisper small` is not adequate; he expects `speech_indicconformer` to be
    the Telugu path with faster-whisper for Hindi/English, but is not deciding until he has
    compared small / medium / IndicConformer (CTC and RNNT) on real recordings with latency
    (his Week 8 tuning), and will check whether the latency spread is decoder fallback.
    **At his request the orchestrator is NOT pointed at a per-language ASR split yet** (it
    would need to choose the transcribe URL by declared language; not built). The size of
    the risk is unmeasured: the test speech was synthetic and one sentence.

32. **The elder app has no way to obtain a signed token, so `AUTH_MODE=jwt` cannot be
    switched on for staging yet.** The app mints its own random UUID in the browser
    (`crypto.randomUUID()`, `window.__satToken`) and sends it as the Bearer token; nothing
    server-side ever issued it. The only issuer is onboarding's `/activate` (a family member
    invites; the elder scans a QR), which now also returns a signed `access_token` -- but
    the elder app does not call onboarding, and Caddy does not route `/onboarding*`. The 36
    users on staging hold client-made UUIDs, so a cutover also strands them. Options, a
    decision for M1 (client) and M4/the supervisor (is open self-registration acceptable?):
    (a) the elder app uses the QR onboarding flow; (b) a self-registration endpoint that
    creates a user and returns a token -- simple, but then anyone can mint accounts, which is
    exactly as open as the stub is today; (c) keep the UUID for existing users and require
    signed tokens only for moderators/admins (a role-based `AUTH_MODE`). Not built: no
    endpoint was added without that decision. A moderator today gets a token from an
    operator: `python -m app.tokens <user-uuid>`.

33. **What a signed token still does not do.** No revocation (a stolen token works until it
    expires, 30 days by default, `AUTH_TOKEN_TTL_SECONDS`), no refresh, no secret rotation.
    `JWT_SECRET` signs both session tokens and onboarding invites (the formats cannot be
    confused, but a weak secret weakens both; CI uses a 21-character placeholder and nothing
    enforces a minimum length). The WebSocket takes the token in the URL query (`?token=`,
    because browsers cannot set headers on a handshake), so it can appear in proxy and access
    logs; a short-lived ticket exchanged over HTTP first would avoid that. An unknown `sub`
    is a 401 and is not provisioned, which is the intended change from the stub.
