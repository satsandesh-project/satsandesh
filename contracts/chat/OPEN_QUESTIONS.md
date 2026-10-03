# Open questions for the chat-contract review

Things not settled unilaterally because they depend on decisions other
members own, or on team agreement about trade-offs. The contract ships as a
proposal against these; nothing below blocks the client developer building
against `contracts/chat/mock/app.py` today, but all of it affects the real
shape once settled. Mirrors `services/ai/OPEN_QUESTIONS.md`.

1. ~~**`MessageOut` has no `media_ref`.**~~ **Closed, Week 6.**
   `MessageOut.media_ref` was added (`CONTRACTS_VERSION` bumped to
   `0.2.0`) — see `DECISIONS.md` #15. A `kind: "voice"` message now has a
   read-side wire representation of where its audio is, matching
   `MessageIn.media_ref` on the write side.

2. **The mock does not deduplicate retried `client_msg_id`s.**
   Design decision #2 in `README.md` justifies `client_msg_id` specifically
   as an idempotency key for offline retry. `contracts/chat/mock/app.py`
   does not actually implement that — two `POST /messages` calls with the
   same `client_msg_id` currently create two separate stored messages with
   two different server `id`s. This is fine for a mock demonstrating the
   contract shape, but the real `services/gateway/` implementation needs a
   `(author_id, client_msg_id)` dedup lookup before this guarantee is real.
   Worth confirming: should the mock itself demonstrate dedup (so a client
   developer can test their retry logic against it), or is that out of
   scope for a mock that has no real persistence layer to key off of?

3. **Is a bulk "sync everything since I went offline" endpoint needed?**
   The sync cursor is deliberately per-conversation (`README.md` design
   decision #3). A client with many conversations reconnecting after a long
   offline period currently has to call `GET /messages` once per
   conversation. Worth the team confirming whether that's acceptable for
   the elder-user offline-reconnect use case `services/gateway/`'s Week 3
   work targets, or whether a `GET /sync?since=<per-user timestamp>`
   convenience endpoint (still backed by per-conversation cursors
   underneath) is worth adding.

4. **Is a `GET /circles/{id}/members` route needed?** The task's route
   table doesn't include one, so it isn't implemented — and no
   user-display type exists in this contract right now (see `DECISIONS.md`
   #4; a prior `UserRef` draft was removed for having no caller). If a
   member-listing endpoint is added, should it return bare `user_id`s
   (consistent with `Membership` today, cheap to add) or embedded
   display-name/language projections (saving the client a round trip per
   member, at the cost of a new type)?

5. **Should `source_lang` be validated against a closed set?**
   `DECISIONS.md` #5 explains why `MessageIn.source_lang` is a free string
   rather than `contracts.ai.language.LanguageCode`, to avoid coupling this
   package to `contracts/ai/`. That trades away validation — a typo'd
   language code currently isn't caught at the chat-contract boundary, only
   whenever/if the AI pipeline consumes it. Worth the team weighing that
   trade-off explicitly rather than leaving it as an implicit consequence
   of the decoupling decision.

6. **`CONTRACTS_VERSION` bump policy** — same open question as
   `services/ai/OPEN_QUESTIONS.md` #8: bump-on-any-field-change is the
   current rule. Does the team want semver discipline (major for breaking,
   minor for additive) instead, now that there are two independently
   versioned contract packages in the same repo?

7. **Should the mock's `X-Mock-User-Id`/`?user_id=` convention become the
   permanent mock-auth pattern across `contracts/ai/` and `contracts/chat/`
   mocks, or should chat's mock instead grow a shared fake-JWT helper that
   both mocks (and eventually `services/gateway/app/auth.py`'s real
   implementation) can use?** Not blocking today since only this mock
   currently needs a mock identity concept, but worth deciding before a
   third mock reinvents its own header name.

8. **What is the exact `ModerationAction` → `MessageStatus` mapping?**
   `DECISIONS.md` #6 explains *why* the two enums are kept separate, but
   only sketches the mapping informally (a `BLOCK` decision is "expected
   to drive" `blocked`). It doesn't specify what `NUDGE` or `HOLD` map to,
   whether `ALLOW` alone is sufficient to reach `delivered` or other
   pipeline stages must also complete first, or how a mid-pipeline failure
   unrelated to moderation interacts with a moderation decision that
   hasn't run yet. Left open deliberately: this is M4's (moderation)
   semantics meeting the client's rendering needs (spinner vs. checkmark
   vs. "awaiting review" banner), not something to settle unilaterally
   from the contracts side.

9. **Should `contracts/ai/common.AudioFormat` gain its own `WEBM_OPUS`
   value, or does something transcode real WebM into what it already
   accepts?** `contracts.chat.common.AudioFormat` (Week 6, `DECISIONS.md`
   #14) added `WEBM_OPUS` — what a browser's `MediaRecorder` actually
   produces — because a browser upload needs an honest label regardless of
   what the AI pipeline can currently consume. `contracts/ai/`'s own
   `AudioFormat` doesn't have this value; `services/ai/`'s real ASR
   service (PR #52) already decodes `wav_pcm16` natively and `ogg_opus`/
   `mp3` via ffmpeg, and its own README documents this exact container
   mismatch as an open question, offering the same two resolutions: add
   `WEBM_OPUS` to `contracts/ai/common.AudioFormat` (cheap — the ffmpeg
   decode path already handles real WebM bytes regardless of the label),
   or transcode chat-side `webm_opus` into a format `contracts/ai/`
   already recognizes before ever calling the ASR service (a Week 7
   orchestrator concern). Not settled here — `contracts/ai/` is
   `services/ai/`'s owner's file, not this package's, and this question
   needs that person's (or the team's) agreement, not a unilateral pick
   from the chat-contract side.

## Moderation contract (proposed, issue #65) — open points for M2

`contracts/chat/moderation.py` is M4's proposal for the moderator console's
wire shape, ahead of Week 7. The gateway side (an append-only
`moderation_events` table, its migration, and the routes) is M2's. Four
things need a decision from whoever builds that side:

1. **Where does the mapping from `ModerationAction` to `MessageStatus`
   live?** This package deliberately keeps the two enums apart
   (DECISIONS.md #6), so something has to own `HOLD -> held`,
   `BLOCK -> blocked`, `ALLOW -> sent`. Proposed: the gateway, not either
   contract.

2. **Is `expected_event_id` enforced?** `ModerationReviewIn` carries it so
   a second moderator cannot silently overwrite a decision they never saw.
   That only works if the gateway rejects a stale one with a conflict.

3. **Retention vs. an unresolved moderation state.** `find_expired_media`
   (PR #71) sweeps every media row past `MEDIA_RETENTION_DAYS` with no
   filter on moderation status, so a held or appealed message loses its
   audio at 30 days — the same number as the proposed appeal window. Raised
   on #71; recorded here because this contract is where a status filter
   would get its vocabulary.

4. **Appeals are not in this file.** They are Week 9. The shape above does
   not foreclose them: an appeal is more events on the same message, plus a
   thread the sender can read. Worth confirming that reading is the
   intent before the table is created.

## Renderings (Week 7) — open points

`contracts/chat/renderings.py` and `MessageOut.renderings` (`DECISIONS.md`
#16) are M2's proposal for the receiver-facing output of the orchestrator,
ahead of M1's receiver screen. Three things need a decision from M1 (what the
screen needs) and M3/M4 (what the pipeline can produce):

1. ~~**A voice note's original-language transcript has no read-side home.**~~
   **Closed:** M1 asked for a separate field, now `MessageOut.transcript` /
   `transcript_language` (`DECISIONS.md` #17, `CONTRACTS_VERSION` `0.4.0`).
   The original discussion follows for the record.
   `MessageOut.text` is `null` for a voice message, and so is
   `ModerationQueueItem.original_text`. So a Telugu receiver of a Telugu voice
   note gets the audio and nothing to read — and the ASR transcript, which the
   pipeline computes anyway, is thrown away on the read side. Options: a
   `MessageOut.transcript` (+ language) field; or let `renderings` include an
   entry for the source language whose `text` is the transcript and `audio` is
   `null`. The second reuses what exists but bends "a rendering is derived
   from the pivot"; the first is one more field on every message. Not decided
   here — it changes M1's accessibility story and M4's console, not just
   storage.

2. ~~**Which languages get rendered, and what does the language picker offer?**~~
   **Decided for v1 (M1, on #83): render only the recipients' preferred
   languages**; the receiver screen offers only languages present in
   `renderings`, and the original is always one tap away. Revisit if the
   picker proves to need more. The original discussion follows.
   The render service takes a list of target languages, so the orchestrator
   chooses. Rendering only the languages the recipients prefer is cheapest, but
   then a receiver who picks another language in the UI finds no rendering and
   gets the original. Rendering all three supported languages for every
   message always has something to offer, at three times the render cost
   (CPU TTS on a shared server). A third option is an on-demand
   `POST /messages/{id}/renderings?language=`. The contract as written works
   with any of them — a client only offers languages present in `renderings` —
   but the screen's behaviour differs.

3. ~~**What if renderings are produced or replaced after delivery?**~~
   **Decided for v1 (M1, on #83): fixed at delivery is fine**; re-rendering
   after a model upgrade can be its own change. The original discussion
   follows. The
   contract assumes they exist by the time a message is delivered (the
   pipeline gates delivery). A later re-render (a better model, a language
   added) or a late-arriving rendering has no way to reach a client that
   already holds the message: there is no frame for it. Worth confirming
   that "renderings are fixed at delivery" is acceptable for v1, rather than
   discovering it when the first model upgrade lands.
