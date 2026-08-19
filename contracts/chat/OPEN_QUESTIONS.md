# Open questions for the chat-contract review

Things not settled unilaterally because they depend on decisions other
members own, or on team agreement about trade-offs. The contract ships as a
proposal against these; nothing below blocks the client developer building
against `contracts/chat/mock/app.py` today, but all of it affects the real
shape once settled. Mirrors `services/ai/OPEN_QUESTIONS.md`.

1. **`MessageOut` has no `media_ref`.** The task's field list for
   `MessageOut` is `id, author_id, target_type, target_id, kind, text,
   created_at, status` — no field for referencing rendered/stored audio.
   Implemented exactly as specified, but this means a `kind: "voice"`
   message currently has no wire representation of *where the audio is* on
   the read side, only on the write side (`MessageIn.media_ref`). Either
   `MessageOut` needs a `media_ref` added (a real contract change, would
   bump `CONTRACTS_VERSION`), or there's a separate mechanism (e.g. a
   follow-up render/fetch call once transcription and TTS finish) that
   hasn't been specified yet.

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
