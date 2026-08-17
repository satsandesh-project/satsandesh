# Design decisions — `contracts/chat/` and `contracts/chat/mock/`

Decisions made unilaterally while this was unblocked-but-unreviewed.
Anything here is a candidate for "why did you do it this way" in PR review
— that's the point of writing it down before the meeting, not after.
Mirrors `services/ai/DECISIONS.md` in spirit; the three decisions the task
specifically called out (polymorphic target, client- vs server-generated
ids, sync cursor scope) are written up in `README.md` instead, with the
reasoning a consumer actually needs; this file covers the rest.

## 1. Mock lives inside `contracts/chat/mock/`, not `services/gateway/mock/`

`services/ai/mock/app.py` sits under the *owning service*
(`services/ai/`), with `contracts/ai/` staying a pure data-shape package.
`contracts/chat/mock/app.py` breaks that pattern — the mock is inside the
contracts package itself. This was an explicit instruction for this task,
not a choice made to match convention, so it's flagged here for visibility
rather than silently deviating from the sibling package's layout.
Consequence: `contracts/chat/` has a runtime dependency (FastAPI) that
`contracts/ai/` does not; the actual chat *models* (`common.py`,
`messages.py`, `circles.py`, `envelope.py`, `errors.py`) remain
dependency-free Pydantic, same as `contracts/ai/`.

## 2. Tests live in `services/gateway/tests/`, not inside `contracts/chat/`

`services/ai/DECISIONS.md` #11 keeps all tooling config and tests inside
the owning *service* directory, leaving `contracts/` and `services/` as
plain namespace packages with no `pyproject.toml`, `conftest.py`, or
`.gitignore` of their own beyond a bare `__pycache__` ignore. `contracts/chat/`
follows that same rule even though its mock lives inside the contracts
package (decision #1 above): `services/gateway/pyproject.toml` already has
every dependency the chat tests need (`pydantic`, `fastapi`, `httpx`), so
adding a second packaging setup inside `contracts/chat/` would be pure
duplication for no gain. Test files are prefixed `test_contracts_chat_*`
to stay distinguishable from `services/gateway/`'s own `app/` tests in the
same directory.

## 3. `MessageOut.id` / `AckOut.id` are opaque strings, not UUIDs

`MessageIn.client_msg_id` is typed `UUID` (any client library can generate
one; validation catches malformed values immediately). The server-assigned
`id`, by contrast, is a plain `str`.

**Why:** a real implementation will likely want a *sortable* id scheme
(UUIDv7, ULID, KSUID, or a per-conversation monotonic sequence) so message
order can be read directly off the id instead of requiring a
`created_at` tiebreaker. Constraining `id` to UUID (v4, non-sortable) in
the contract today would foreclose that choice before the database owner
has made it. Same reasoning as `contracts/ai/common.py`'s `AudioRef.uri`
staying an unconstrained string until a storage layer is chosen — see
`services/ai/DECISIONS.md` #10.

## 4. `UserRef`, not the gateway's `User`, for chat display purposes

The task asked to reuse `services/gateway/app/models.py`'s `User` "if it
fits." **It doesn't, for two reasons:**

- **Layering.** `services/ai/DECISIONS.md` #11 establishes the pattern this
  whole repo follows: `contracts/` packages have zero dependency on any
  `services/` package — services depend on contracts, never the reverse.
  Importing `app.models.User` from `contracts/chat/` would invert that for
  this one package, and `services/gateway/` will eventually want to import
  `contracts/chat/` too, which would make the dependency circular.
- **Field/shape mismatch.** `app.models.User` carries `role: Literal["elder",
  "moderator", "admin"]` — an authorization concept, not something a chat
  payload needs to leak — and uses bare `str` for `id`/`preferred_language`
  where this package's own conventions favor typed/closed fields where
  practical.

Instead, `contracts/chat/common.py` defines `UserRef` (`id`, `name`,
`preferred_language`) — a minimal, decoupled display projection, same
relationship `contracts/ai/envelope.py` has to the real gateway envelope
(a standalone proposal, not a dependency). **Currently unused by any route
in this contract** — none of `MessageOut`/`Circle`/`Membership` embed it,
per the task's literal field list — see `OPEN_QUESTIONS.md` for whether a
future member-listing endpoint should return `UserRef` instead of a bare
`user_id`.

## 5. `contracts/chat/` does not import `contracts/ai/`

`MediaRef` (this package) and `AudioRef` (`contracts/ai/common.py`) cover
overlapping ground — both reference stored media by URI. `contracts/chat/`
deliberately does not import `contracts/ai/common.AudioRef` or
`contracts.ai.language.LanguageCode` (used loosely for `source_lang`)
despite the overlap.

**Why:** the two contract packages are owned and versioned independently
(see `CONTRACTS_VERSION` note in `common.py`). Importing across them would
mean a `contracts/ai/` shape change could force an unrelated version bump
or breaking change onto `contracts/chat/` consumers, for a coupling that
buys nothing — `MediaRef` here doesn't need `AudioFormat`'s enum or
`AudioRef`'s sample-rate/duration precision, and `source_lang` doesn't need
`LanguageCode`'s closed three-language enum, since a text message's source
language is metadata for the AI pipeline to interpret, not something this
contract enforces. If the two are later found to need the exact same
shape, promoting a value object to a third, shared package is a smaller
change than un-coupling two packages that were never meant to depend on
each other.

## 6. `MessageStatus` is a separate enum from `contracts.ai.moderation.ModerationAction`

`ModerationAction` (`ALLOW`/`NUDGE`/`HOLD`/`BLOCK`) is what a moderation
decision *recommends*. `MessageStatus` (`pending`/`delivered`/`held`/
`blocked`/`failed`) is the chat-level delivery lifecycle a client renders
(spinner, checkmark, "awaiting review" banner). They're related — a
`BLOCK` decision is expected to drive a message to `blocked` — but not
identical: `MessageStatus` also covers states moderation has no opinion on
(`pending` while transcription/translation is still running, `failed` on a
pipeline error that has nothing to do with content). Keeping them separate
means the gateway's status-transition logic can consume a
`ModerationDecision` as one input among several, not treat it as the
literal wire value the client sees.

## 7. Sync cursor trade-off (expands on `README.md`'s design decision #3)

Per-conversation cursors mean a client resuming after being offline for a
while must call `GET /messages` (or send `sync.request`) once per
conversation it cares about, not once globally. This was accepted as the
right trade-off for the reason in `README.md` (a global cursor can't
express per-conversation read state) — see `OPEN_QUESTIONS.md` for whether
a bulk "sync every conversation I'm a member of" convenience endpoint is
worth adding on top, without changing the per-conversation cursor
semantics underneath it.

## 8. Enum, not a bare string, for every closed set

`TargetType`, `MessageKind`, `MessageStatus`, `FrameType`, `ErrorCode`, and
`MembershipRole` are all closed Pydantic enums. Same reasoning as
`services/ai/DECISIONS.md` #2: a typo fails at the contract boundary
instead of silently mis-routing a message downstream. `source_lang` is the
one deliberate exception — see decision #5 above for why it stays a free
string instead of reusing `contracts.ai.language.LanguageCode`.

## 9. `contract_version` lives on top-level payloads only

`VersionedModel` is the base for every request/response/frame-data model,
but not for nested value objects (`UserRef`, `MediaRef`). Same reasoning as
`services/ai/DECISIONS.md` #5 — a `MediaRef` embedded inside a `MessageIn`
is never validated or transmitted standalone, so its own version stamp
would be noise.

## 10. `kind`/payload consistency is enforced by the model, not left to callers

`MessageIn` rejects `kind: "text"` with no `text`, and `kind: "voice"` with
no `media_ref`, via a `model_validator`. This was chosen over documenting
the constraint and trusting callers to honor it, so a malformed request
fails at the contract boundary (a 422 from the mock) instead of reaching
the service layer with an ambiguous "voice message with no audio" state to
handle.

## 11. `CircleCreate`/`MembershipCreate` are separate models from `Circle`/`Membership`

Server-assigned fields (`id`, `created_by`, `created_at`, `circle_id`,
`joined_at`) are absent from the `*Create` request models entirely, rather
than present-but-ignored-if-sent. A client cannot construct a request that
claims a `created_by` or `id` — those come from the authenticated caller
and the URL path respectively, not the body, so they can't be spoofed by
sending a plausible-looking value.

## 12. No new root-level packaging files

Consistent with `services/ai/DECISIONS.md` #11: no root `pyproject.toml`,
`pytest.ini`, or `.gitignore` was added. `contracts/` remains a PEP 420
namespace package (no `__init__.py` at that level); `contracts/chat/`
itself has one (empty), matching `contracts/ai/__init__.py`. All new
tooling (fixture generator, tests) lives inside `services/gateway/`, which
already owned the only `pyproject.toml` this task's scope allowed touching.
