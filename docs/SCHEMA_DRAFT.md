# Schema draft — `users`, `circles`, `memberships`, `messages`

Week 2 data-model design, owned by M3. **This is a design document, not
code** — no SQLAlchemy models, no Alembic config, no migrations here. That
comes after this is reviewed.

**Source material:** the proposal's Section 8 data-model sketch (`users`:
id, name, photo, preferred_language, tts_on, role; `circles`;
`memberships`; `messages`: author, target, original_media_ref, source_lang,
pivot_text_en, status), cross-checked against the actual wire contract in
`contracts/chat/` and the UUIDv7 id-scheme decision in `docs/DECISIONS.md`.
Where the proposal's sketch and the shipped contract disagree, it's called
out explicitly in the relevant table's section below, not silently
resolved one way.

## Conventions used throughout

- **Primary keys:** `uuid`, generated app-side as UUIDv7 (`docs/DECISIONS.md`
  — "Chat message `id` is UUIDv7"). That decision was argued specifically
  for `messages.id` because it's the sync cursor; extending it to
  `users.id` and `circles.id` too is a smaller, lower-stakes call — neither
  table has a pagination/cursor need today (no user-listing route exists;
  `GET /circles` returns an unordered list). Recommended anyway, for one
  generation helper used everywhere and the incidental benefit UUIDv7 gives
  any btree primary key (insert locality — new rows cluster at the end of
  the index instead of scattering randomly, which is a real, if secondary,
  win for `memberships` and `circles` too). Not load-bearing the way
  `messages.id` is; flag it if the team disagrees.
- **Timestamps:** `timestamptz`, never bare `timestamp`. A naive timestamp
  can't be correctly converted to a user's local time — directly relevant
  to the Phase 7 quiet-hours timezone question below — and silently
  assumes server-local time across a DST change. Every `*_at` column in
  this draft is `timestamptz`.
- **Strings:** `text`, no `varchar(n)` caps — matches the contract's own
  unconstrained-string philosophy (`contracts/chat/DECISIONS.md` #5, #8).
- **Closed sets** (`role`, `status`, `kind`, `target_type`): `text` +
  `CHECK ... IN (...)`, not a native Postgres `ENUM` type. Native enums are
  painful to evolve — adding a value is an `ALTER TYPE ... ADD VALUE`
  that can't run inside the same transaction as other DDL in older
  Postgres, and removing/reordering values isn't supported at all. A
  `CHECK` constraint is a one-line `ALTER TABLE ... DROP CONSTRAINT` /
  `ADD CONSTRAINT` — cheap to change when the contract's enums change,
  which is expected to happen (`contracts/chat/OPEN_QUESTIONS.md` #8 alone
  implies `MessageStatus`'s transition rules aren't final).
- **Media/URIs:** `text` — matches `contracts/chat/common.py`'s
  `MediaRef.uri: str`. No object-storage-specific column type is imposed
  here that the contract layer doesn't already impose.

## `id` as native `uuid`, not `text` — confirmed, and why it matters

Every primary key and every foreign key in this draft is Postgres's native
`uuid` type. This is not interchangeable with storing the same 36-character
string in a `text` column, and the difference is exactly what makes
`id > since_id` mean "generated after" for UUIDv7.

**Native `uuid`:** stored as its raw 16-byte binary value. Comparison
operators (`<`, `>`, `=`) compare those 16 bytes directly, most-significant
byte first. UUIDv7's layout puts a 48-bit big-endian millisecond timestamp
in the first 6 bytes, so a byte-wise compare of two UUIDv7 values *is* a
compare of their generation timestamps (with the trailing random/counter
bits breaking ties within the same millisecond — see
`contracts/chat/DECISIONS.md` #3). `id > since_id` is therefore exactly
"rows generated after the cursor," which is what the sync query
(`WHERE ... AND id > since_id ORDER BY id`) depends on.

**What breaks if `id` is stored as `text` instead:**

- **Collation, not byte order, decides the comparison.** A `text` column
  compares under whatever collation the column/database has — Postgres
  defaults to a locale collation (e.g. `en_US.UTF-8`), not `C`. Locale
  collations are not guaranteed to sort ASCII characters in raw byte
  order for every character class, so `id > since_id` on a `text` column
  is not provably equivalent to "generated after" unless the column is
  explicitly pinned to `C` collation — a detail nothing about a plain
  `text` column enforces or documents at the schema level.
- **Case and formatting aren't enforced.** `uuid`'s input function
  normalizes and validates every value on write — badly-formed input is
  rejected outright. `text` accepts anything: a differently-cased hex
  digit (`018F...` vs `018f...`) or non-canonical formatting from one
  stray manual `INSERT`, admin script, or migration tool sorts differently
  under byte/`C` comparison than the app's consistently-lowercase output,
  silently corrupting cursor order for exactly the rows affected — with no
  error at insert time and no obvious symptom until a client's sync skips
  or duplicates a message.
- **Storage and comparison cost double, for no upside.** `uuid` is a fixed
  16 bytes. The canonical hyphenated string is 36 characters — `text`
  storage is at minimum 2.25x larger per value, in every row and every
  index entry, which means a bigger `messages` primary-key index, worse
  cache locality, and slower range scans on the exact index the sync query
  depends on. Locale-aware string comparison (as opposed to a byte/integer
  compare) is also measurably slower per comparison at the engine level.

None of this is hypothetical edge-case pedantry — it's the difference
between "UUIDv7 as id scheme" being a real, enforced property of the
schema versus a convention every future writer has to remember to uphold
by hand. Use `uuid`.

## `users`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NOT NULL, **PK** | UUIDv7, app-generated |
| `name` | `text` | NOT NULL | |
| `photo` | `text` | NULL | URI/ref to stored image — same "reference, not embedded bytes" pattern as `MediaRef.uri`. Proposal field; not present in `services/gateway/app/models.py`'s stub `User` today |
| `preferred_language` | `text` | NOT NULL | Free string, not constrained to `contracts.ai.language.LanguageCode`'s closed 3-language enum — see design question #6 below for whether that's right |
| `tts_on` | `boolean` | NOT NULL DEFAULT `true` | Proposal field; also not in the gateway's stub `User` model |
| `role` | `text` | NOT NULL DEFAULT `'elder'` | `CHECK role IN ('elder','moderator','admin')`. This is the **global/site** role, matching `services/gateway/app/models.py::User.role`. Deliberately called out: `memberships.role` below reuses the words `'moderator'`/`'admin'` for a **per-circle** role — same vocabulary, different scope. Worth a naming comment when this becomes code, so nobody reads one as implying the other. |
| `timezone` | `text` | NULL | **Not in the proposal's sketch.** Recommended addition — see design question #4 |
| `created_at` | `timestamptz` | NOT NULL DEFAULT `now()` | Not in the proposal's sketch either; standard audit column, negligible cost to add |

**Disagreement flagged:** the proposal's `role` and the gateway's existing
`User.role` agree with each other, but neither the proposal nor the
contract says anything about how a user authenticates (phone, email, OTP).
Not modeled here — `services/gateway/app/auth.py` is still a stub per its
own README, and inventing auth columns speculatively ahead of that work is
exactly the kind of premature design this repo's `DECISIONS.md` files
elsewhere argue against. Flagged so it isn't mistaken for an oversight.

**Primary key:** `id`. **Foreign keys:** none outward; referenced by
`circles.created_by`, `memberships.user_id`, `messages.author_id`.

**Indexes:** none beyond the primary key. No query in the current contract
or route table looks a user up by `name`, `preferred_language`, or `role`
— every reference to a user elsewhere is by `id`.

## `circles`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NOT NULL, **PK** | UUIDv7, app-generated |
| `name` | `text` | NOT NULL | |
| `created_by` | `uuid` | NOT NULL, **FK** → `users.id` | Matches `contracts/chat/circles.py::Circle.created_by` |
| `created_at` | `timestamptz` | NOT NULL DEFAULT `now()` | |

**Primary key:** `id`. **Foreign key:** `created_by → users.id`, `ON DELETE
RESTRICT` (a circle shouldn't end up pointing at a nonexistent creator;
revisit if account deletion becomes a real feature — see the aside under
design question #2).

**Indexes:** none beyond the primary key today. No route filters circles
by `created_by`. (`GET /circles` scoping by membership is a `memberships`
index concern — see below.)

## `memberships`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `circle_id` | `uuid` | NOT NULL, **PK (1/2)**, **FK** → `circles.id` | |
| `user_id` | `uuid` | NOT NULL, **PK (2/2)**, **FK** → `users.id` | |
| `role` | `text` | NOT NULL DEFAULT `'member'` | `CHECK role IN ('member','moderator','admin')` — matches `contracts/chat/circles.py::MembershipRole` |
| `joined_at` | `timestamptz` | NOT NULL DEFAULT `now()` | |

**Primary key:** composite `(circle_id, user_id)` — **no separate `id`
column**, deliberately: `contracts/chat/circles.py::Membership` has no
`id` field on the wire at all, so storage shouldn't invent one the API
never exposes. The composite pair is exactly what makes a membership
unique in the first place.

**Foreign keys:** `circle_id → circles.id`, `ON DELETE CASCADE` (a
membership can't outlive its circle); `user_id → users.id`, `ON DELETE
RESTRICT`.

**Indexes:**
- Primary key `(circle_id, user_id)` — serves "is user X a member of
  circle Y" (authorization check when posting a message with
  `target_type='circle'`) and "list all members of circle Y," both led by
  `circle_id`.
- `(user_id, circle_id)` — **not covered by the PK** (a btree on
  `(circle_id, user_id)` can't efficiently serve a `WHERE user_id = :x`
  lookup without `circle_id`). Needed for "list circles user X belongs
  to" — `SELECT circle_id FROM memberships WHERE user_id = :user_id`.
  This is the query a real `GET /circles` needs: `contracts/chat/mock/app.py`
  currently returns *every* circle unconditionally (`list(_circles.values())`),
  which is a mock simplification, not a modeled decision — nothing sane
  would return the entire platform's circles to every caller in a real
  implementation. Flagging here because it's the reason this index exists
  even though no real route demonstrates the query yet.

## `messages`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `uuid` | NOT NULL, **PK** | UUIDv7, app-generated at insert time — see `docs/DECISIONS.md` |
| `client_msg_id` | `uuid` | NOT NULL | Client-generated idempotency key, matches `MessageIn.client_msg_id` |
| `author_id` | `uuid` | NOT NULL, **FK** → `users.id`, `ON DELETE RESTRICT` | |
| `target_type` | `text` | NOT NULL | `CHECK target_type IN ('user','circle')` |
| `target_id` | `uuid` | NOT NULL* | *Storage shape pending — see design question #1 |
| `kind` | `text` | NOT NULL | `CHECK kind IN ('text','voice')` |
| `text` | `text` | NULL | Source-language text: authored directly for `kind='text'`, or the ASR transcript for `kind='voice'` — populated once transcription resolves. Matches `contracts/chat/README.md`'s documented behavior ("`text` is `null` on a `voice` message until transcription resolves it") |
| `pivot_text_en` | `text` | NULL | English MT intermediate — `contracts/ai/pivot.py::PivotResponse.pivot_text`. **Proposal field with no current wire exposure**; `MessageOut` doesn't carry it and isn't expected to (a client renders its own recipient-language text, not the English pivot) — kept here for pipeline reuse (re-render without re-translating from source) and so moderation can run once against a stable English pivot instead of once per recipient language |
| `original_media_ref` | `text` | NULL, required when `kind='voice'` | URI to the stored source audio |
| `media_duration_ms` | `integer` | NULL | Flattened out of the wire's nested `MediaRef.duration_ms` |
| `source_lang` | `text` | NULL | Free string, matches `MessageIn.source_lang` |
| `status` | `text` | NOT NULL DEFAULT `'pending'` | `CHECK status IN ('pending','delivered','held','blocked','failed')` — matches `contracts/chat/common.py::MessageStatus` exactly |
| `deleted_at` | `timestamptz` | NULL | **Not in the proposal's sketch.** Recommended addition — see design question #2 |
| `created_at` | `timestamptz` | NOT NULL DEFAULT `now()` | |

**Disagreement flagged — media column naming:** the proposal's sketch
names this column `original_media_ref`; the wire contract's field
(`MessageIn.media_ref`) has no `original_` prefix. Recommendation: keep
the wire field as-is (it's shipped, versioned, and renaming it is a
contract-breaking change for no consumer benefit) but use the proposal's
more precise `original_media_ref` for the **storage** column — the prefix
earns its keep once per-recipient-language rendered audio potentially
needs its own storage too (see design question #5); an unprefixed
`media_ref` column would then be ambiguous between "the source audio" and
"a rendered audio," which the wire field name isn't exposed to but the
schema is.

**Disagreement flagged — text representations:** the proposal's sketch
lists one `pivot_text_en` column in addition to whatever holds the
original text; the wire contract exposes exactly one `text` field on
`MessageOut`. Those aren't actually in conflict once `text` is understood
as *source-language* text (confirmed by the README's transcription-timing
note above) and `pivot_text_en` as a separate, wire-invisible pipeline
column — but the proposal's sketch doesn't have a place for the
**per-recipient-language** text and audio that `contracts/ai/render.py`'s
`RenderResult` produces (one `{language, text, audio}` per target
language, fanned out from a single `pivot_text`). Neither this table nor
the proposal's sketch has a slot for that. Not resolved here — see design
question #5.

**Primary key:** `id`. **Foreign keys:** `author_id → users.id`; `target_id`'s
FK (if any) depends on design question #1.

**Constraints:**
- **`UNIQUE (author_id, client_msg_id)`** — the idempotency guarantee
  `contracts/chat/README.md` design decision #2 promises but
  `contracts/chat/mock/app.py` doesn't yet implement
  (`contracts/chat/OPEN_QUESTIONS.md` #2). Two `POST /messages` calls from
  the same author with the same `client_msg_id` must resolve to the same
  row, not create a duplicate. Scoped to `(author_id, client_msg_id)`
  rather than a bare `UNIQUE (client_msg_id)` deliberately: the guarantee
  this needs to hold is per-sender retry safety (an elder's client
  re-sending after a dropped ack on an unreliable network — the exact
  scenario `README.md` #2 names), not global collision-avoidance across
  every user's independently-generated client-side UUIDs, which nothing
  architecturally requires.
- **`CHECK ((kind = 'text' AND text IS NOT NULL) OR (kind = 'voice' AND original_media_ref IS NOT NULL))`**
  — mirrors `MessageIn`'s `model_validator` (`contracts/chat/messages.py`,
  `DECISIONS.md` #10) at the storage layer, so a write that bypasses the
  Pydantic boundary entirely (a migration backfill, an admin script, a
  future batch job) can't silently create the "voice message with no
  audio" state that validator exists specifically to keep out.
- `target_id`'s constraint (FK vs. no FK) — pending design question #1.

**Indexes:**
1. Primary key `(id)` — `uuid`, UUIDv7. Chronological by construction; see
   the confirmation section above.
2. `UNIQUE (author_id, client_msg_id)` — same index that backs the
   constraint above; also what a `POST /messages` handler queries (or
   `ON CONFLICT`s against) before insert to decide "is this a retry."
3. **`(target_type, target_id, id)`** — the sync query, and the reason
   `id` had to be settled before this schema could be written:
   ```sql
   SELECT * FROM messages
   WHERE target_type = :target_type AND target_id = :target_id
     AND id > :since_id
   ORDER BY id
   LIMIT :limit
   ```
   This is `GET /messages?target_type=&target_id=&since=&limit=` and the
   `sync.request`/`sync.batch` WS pair (`contracts/chat/envelope.py`'s
   `SyncRequest.since_id`) — the single most load-bearing query in this
   schema. `id` trailing in the index means the same index serves both the
   `WHERE id > :since_id` range filter and the `ORDER BY id`, with no
   separate sort step, precisely because UUIDv7 makes `id` order equal
   chronological order (`contracts/chat/DECISIONS.md` #3).

No standalone index on `author_id` — no route today lists "all messages by
author X" independent of a target.

## Design questions for the team, before I write migrations

Recommendation given for each; none of these are settled by this
document.

### 1. How is `target` modeled at the DB level?

The wire contract already settled the *API* shape — a single polymorphic
`(target_type, target_id)` pair (`contracts/chat/DECISIONS.md` #1). That
doesn't settle the *storage* shape, and the wire decision explicitly
punted this exact question to the service layer: "the wire contract can't
express `target_id` must be a valid circle when `target_type` is circle...
that's a referential-integrity concern... that belongs in
`services/gateway/`." This is that concern, now in front of the person who
owns it.

- **(a) Polymorphic, no FK.** `target_id uuid NOT NULL`, `target_type`
  discriminates which table it means, exactly mirroring the wire shape.
  Simple, but nothing at the DB level catches a message left pointing at a
  deleted circle or a typo'd id — that check has to live entirely in the
  app layer, every time, forever.
- **(b) Two nullable FK columns.** `target_user_id uuid NULL REFERENCES
  users(id)`, `target_circle_id uuid NULL REFERENCES circles(id)`, plus
  `CHECK (num_nonnulls(target_user_id, target_circle_id) = 1)`. Real
  referential integrity — a dangling target is impossible by construction,
  not by app-layer discipline. The app layer projects this back to the
  wire's single `(target_type, target_id)` pair on read; the two shapes
  never need to look the same in storage and on the wire.

**Recommendation: (b).** The wire contract's own reasoning for accepting a
referential-integrity gap was "that's a DB concern, not a Pydantic one" —
which argues for closing it at the DB, not leaving it open there too. The
extra column is a small, one-time cost; a message silently addressed to a
circle that no longer exists is a real failure mode this product will hit
once circles can be deleted or renamed, not a hypothetical.

### 2. Should messages be soft- or hard-deleted?

Not in the proposal's sketch or the wire contract at all — `status`
(`pending`/`delivered`/`held`/`blocked`/`failed`) covers pipeline and
moderation state, not "this message was removed after the fact."

**Recommendation: soft delete**, via the `deleted_at` column above,
default `NULL`; every list/sync query adds `AND deleted_at IS NULL`.
Reasoning: `held`/`blocked` already exist for moderation take-down, which
argues a moderator or system actor removing a message should be a
`status` transition, not a delete — but a message being soft-deleted is a
separate, later action from either the author or a moderator, and
overloading `status` to also mean "gone" conflates two different
timelines (why is this message hidden, vs. when was it hidden). Hard
delete also interacts badly with the sync cursor: a client resuming with a
`since_id` that spans a hard-deleted row's position sees a gap it can't
distinguish from data loss, whereas a soft-deleted row can still be
represented (or simply filtered, leaving no id-order gap the client has to
reason about) without losing the moderation audit trail a real moderation
review flow will eventually want.

*Aside, not a full question:* the same soft-vs-hard question likely
applies to `users` (account deactivation vs. deletion) — not asked, but it
directly affects whether `author_id`'s `ON DELETE RESTRICT` above is the
right call long-term. Flagging so it isn't forgotten, not answering it
here.

### 3. What `status` transitions are valid, and who's allowed to make them?

`contracts/chat/DECISIONS.md` #6 explains *why* `MessageStatus` is a
separate enum from `contracts.ai.moderation.ModerationAction`, but not the
actual mapping — `contracts/chat/OPEN_QUESTIONS.md` #8 already logs that
as open (what does `NUDGE` map to? does `ALLOW` alone reach `delivered`?).
This is that same gap's storage-layer twin: does the schema need a
transition/audit table (`message_status_history`: `message_id`,
`from_status`, `to_status`, `changed_by`, `changed_at`, `reason`), or is
overwriting `status` in place enough for Phase 6?

**Recommendation:** don't build the history table now — nothing in the
current contract or proposal calls for showing status history to anyone,
and speculative tables are exactly what this repo's `DECISIONS.md` files
elsewhere argue against building ahead of a real caller
(`contracts/chat/DECISIONS.md` #4). But flag it explicitly rather than
silently dropping it: once M4's moderation review tooling exists, a
moderator looking at a `held` message will plausibly want to know when and
why it got there, and adding the history table later is a pure addition
(new table, zero change to `messages`) — cheap to defer, not cheap to
retrofit if it turns out `status` needs to record a reason string too.

### 4. Do `users` need a `timezone` column now, for Phase 7's quiet hours?

**Recommendation: yes, add it now**, nullable, unused by any Week 2/3 code
path. Same shape of argument as the UUIDv7 decision in
`docs/DECISIONS.md`: a nullable, currently-unused column costs nothing
today. Retrofitting it once users already exist means either prompting
every existing user for their timezone after the fact or guessing from
weaker signals (phone country code, IP history) — worse in every way than
just having the column ready to populate whenever Phase 7's collection
mechanism (signup prompt, explicit setting, inferred — a product question
outside this doc's scope) is decided.

### 5. Where do per-recipient-language renders live, and what does `MessageOut.text` actually represent?

Not one of the four minimum questions, but surfaced directly by
cross-checking the proposal's sketch against `contracts/ai/render.py`:
`RenderRequest`/`RenderResult` fan a single `pivot_text` out to **one
`{language, text, audio}` result per target language** in a circle. The
proposal's flat `messages` row (one `pivot_text_en` column) has no slot
for N per-language results, and neither does this draft as written.

Two different places that data could live, and they have different
consequences for this schema:

- **A child table**, e.g. `message_renders (message_id, language, text,
  audio_ref, model_version_translate, model_version_tts, created_at)`,
  `PRIMARY KEY (message_id, language)` — normalized, sits naturally next
  to `messages` in this same database.
- **Not persisted here at all** — computed on demand or cached in
  whatever store the AI service (M4's `services/ai/`) already owns,
  treating `services/gateway/`'s Postgres as a chat-metadata store only,
  not a cache for AI pipeline outputs.

**No recommendation given** — this is genuinely M4's call as much as
mine: it depends on whether re-rendering on every read is acceptable
latency/cost for the AI service, which I don't own enough context on to
decide unilaterally. Flagging it now because it directly determines
whether `MessageOut.text` (currently: source-language text only, per the
column definition above) should ever grow a second, recipient-language
representation — a contract question, not just a storage one.

### 6. Should `users.preferred_language` be constrained to `contracts.ai.language.LanguageCode`'s 3-language enum?

`MessageIn.source_lang` is deliberately a free string, not
`contracts.ai.language.LanguageCode`, specifically to avoid coupling
`contracts/chat/` to `contracts/ai/` (`contracts/chat/DECISIONS.md` #5).
`users.preferred_language` in this draft follows that same precedent — but
the reasoning underneath it is different: a message's `source_lang` is
metadata the AI pipeline interprets or doesn't, but a **user's**
`preferred_language` directly drives which language the render pipeline
must produce for them, and `contracts/ai/language.py` only supports
`en`/`hi`/`te` for v1 (Tamil/Kannada explicitly deferred). A user whose
`preferred_language` is set to an unsupported code has no render path at
all — a real, not hypothetical, failure mode.

**Recommendation:** worth the team's explicit call rather than defaulting
to the same free-string precedent for a different reason than the one that
motivated it. Leaning toward *not* hard-constraining the column to the
3-language enum at the DB level (same layering argument as
`contracts/chat/DECISIONS.md` #5 — this table shouldn't import
`contracts/ai/`'s vocabulary and churn every time v2 adds a language), but
validating it at the application layer against whatever languages
`services/ai/` currently supports, so the failure is a rejected signup/
settings-change, not a silently-unrenderable user discovered later.
