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

**Reversal cost:** Low. A file move (`app.py` to
`services/gateway/mock/`) plus fixing the uvicorn invocation and any
relative imports. No wire shape changes, nothing a consumer of the mock
observes from outside the process.

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

**Reversal cost:** Low. Moving the test files back under
`contracts/chat/` and giving it its own `pyproject.toml`/`conftest.py` is
mechanical and additive — no behavior change, no consumer impact. It's
slightly more than a pure file move only because it requires standing up
the packaging surface this decision explicitly avoided duplicating.

## 3. Server-assigned `id` is a time-sortable id (UUIDv7), not random UUIDv4

`MessageIn.client_msg_id` is typed `UUID` (any client library can generate
one; validation catches malformed values immediately). The server-assigned
`id`, by contrast, stays a plain `str` on the wire — that part was never in
question, same reasoning as `contracts/ai/common.py`'s `AudioRef.uri`
staying an unconstrained string until a storage layer is chosen (see
`services/ai/DECISIONS.md` #10). What *was* left open is what value the
server puts in that string. It's closed now: a time-sortable id (UUIDv7),
not a random UUIDv4.

I own the Week 2 data model, and this choice determines what Week 3's sync
can do with a cursor — deciding it now, before either exists, is cheaper
than discovering the constraint after the schema is built (see reversal
cost below).

**Coupling to the sync cursor:** `SyncRequest`/`SyncBatch`'s `since`
parameter (`README.md` design decision #3) means "give me everything after
this point." What "this point" *is* depends entirely on the id scheme:

| | (a) random UUIDv4 | (b) time-sortable id (UUIDv7 / ULID) |
|---|---|---|
| Ordering source | `created_at` column — `id` carries no order information | `id` itself — generation order ≈ sort order |
| Cursor shape | Composite `(created_at, id)`; `id` is only a tiebreaker, `created_at` alone isn't unique enough to page on | Scalar: `id` alone |
| Index | `(target_type, target_id, created_at, id)` — three columns; the query needs row-value comparison (`(created_at, id) > (last_created_at, last_id)`, native in Postgres, manual `OR`-chains elsewhere) | `(target_type, target_id, id)` — two columns; a plain `id > since` range scan |
| Wire cost | `since` must carry both fields, so `SyncRequest` needs a second cursor field and the client stores/replays a pair, not one token | `since` is the last `id` the client already has — the same string it already stores per message, nothing new to add |
| Clock skew | Two app-server instances (or an NTP correction) can assign `created_at` values that don't reflect real cross-process insertion order; a `since=` comparison against `created_at` silently *skips* a message stamped "in the past" relative to the cursor, not just misorders it | Skew still exists (the id embeds the *generating* server's clock) but is bounded to inter-node clock drift, not however far apart two independently-sourced `created_at` values land — ordering is a property of the id itself, not a second field that can disagree with it |
| Identical timestamps | Real: DB timestamp precision (ms) plus bursty sends (batch imports, several messages in one request) produce exact `created_at` ties. Correctness depends on *every* query and index consistently using `id` as the tiebreaker — page on `created_at` alone once, and tied rows get dropped or duplicated across pages | Ties inside the same millisecond are broken by the id's own trailing bits (UUIDv7's sub-ms `rand_a`/`rand_b`, or ULID's monotonic-variant per-ms counter) — the tiebreaker already lives in the one column being sorted, not a second column the query layer has to remember to add |

**Recommendation: UUIDv7 over ULID.** `client_msg_id` is already a
standard `UUID` on the wire; keeping the server `id` in the same type
family (a different version byte, not a different shape) means one id
family for both fields instead of "UUID for one, a 26-char base32 string
for the other." Python's stdlib `uuid` module doesn't gain `uuid7()` until
3.14; this repo pins `>=3.11` (`services/gateway/pyproject.toml`), so Week
2's implementation needs a small third-party generator (`uuid6`,
`uuid-utils`) rather than stdlib — a Week 2 implementation detail, not a
contract-shape blocker, since the wire type is `str` either way. ULID
would be the fallback only if the data layer later wants a non-UUID-shaped
sortable string for some other reason (case-insensitivity, a shorter
external identifier); nothing today pushes toward that.

**Consequence for Week 2 and Week 3:** Week 2's `id` column is generated
server-side as UUIDv7 at insert time (never client-supplied, consistent
with `README.md` design decision #2 on `client_msg_id` vs `id`), indexed as
`(target_type, target_id, id)` — no `created_at` in the sync index. Week
3's sync treats `since` as the last message `id` the client holds, full
stop; no `created_at` tiebreaker field gets bolted onto
`SyncRequest`/`SyncBatch` later, because there's no ordering gap left for
it to patch. A schema or sync design built against `created_at` as the
sort key is designing against a shape this contract no longer has —
flagging that now so Week 2 and Week 3 build against the same assumption.

**Reversal cost:** High, once real data exists. The id scheme is baked
into every row at generation time — switching later isn't a config flip,
it's a one-time backfill (re-deriving order for existing rows, only
possible if `created_at` was *also* kept as a fallback column) or living
with pre- and post-migration ids sorting inconsistently against each
other. Every client holding a `since` cursor from before the switch has to
re-sync from scratch, not resume. Before Week 2's schema exists and before
any client holds a stored cursor is the cheap window for this decision —
which is the actual reason to settle it now instead of deferring it.

## 4. No `User`-shaped type in this contract at all

The task asked to reuse `services/gateway/app/models.py`'s `User` "if it
fits." It doesn't — for a layering reason (`services/ai/DECISIONS.md` #11
establishes zero dependency from `contracts/` on any `services/` package;
importing `app.models.User` here would invert that, and `services/gateway/`
will eventually import `contracts/chat/` too, making it circular) and a
field/shape mismatch (`app.models.User` carries `role`, an authorization
concept a chat payload shouldn't leak).

An earlier draft of this package defined a decoupled `UserRef` projection
as the replacement. That was removed: nothing in `MessageOut`, `Circle`,
or `Membership` embeds a user object — every route in the current table
references people by bare `id` string (`author_id`, `created_by`,
`user_id`) — so `UserRef` had no caller and no route exercising it, the
same standard `services/gateway/`'s Week 1 review applied to
`ConnectionManager.send_to_user`/`.broadcast`. If a future route (e.g. a
member-listing endpoint, see `OPEN_QUESTIONS.md` #4) actually needs to
return user display info inline, define the type then, against a real
caller — not now, speculatively.

**Reversal cost:** Low. Adding a display-projection type back is purely
additive — nothing currently depends on its absence, and the earlier draft
was removed cleanly with no wire footprint left behind. The cost is in
designing the type against a real caller when one shows up, not in undoing
anything today.

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

**Reversal cost:** Medium. Adding the import later is a small code change,
but it re-litigates the "independently versioned" invariant the
`CONTRACTS_VERSION` note in `common.py` documents — every subsequent
`contracts/chat/` change would then need to consider whether it also
bumps or is bumped by `contracts/ai/`'s version, a process change, not
just a code change. Anyone who built against the two packages having zero
coupling would need to re-audit that assumption.

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

**Reversal cost:** Medium. Merging the two enums later (e.g. making
`MessageStatus` derive directly from `ModerationAction`) would require
re-deriving the non-moderation states (`pending`, `failed`) from something
else, and any client rendering logic keyed on today's five-value enum
would need remapping. Low cost to keep them separate as they are now; the
real, unresolved cost is the *mapping* between the two enums, which this
decision only sketches informally (`BLOCK` → `blocked`) rather than
specifying in full — see `OPEN_QUESTIONS.md` #8, left open deliberately
since it's M4's (moderation) and the client's call, not mine alone.

## 7. Sync cursor trade-off (expands on `README.md`'s design decision #3)

Per-conversation cursors mean a client resuming after being offline for a
while must call `GET /messages` (or send `sync.request`) once per
conversation it cares about, not once globally. This was accepted as the
right trade-off for the reason in `README.md` (a global cursor can't
express per-conversation read state) — see `OPEN_QUESTIONS.md` for whether
a bulk "sync every conversation I'm a member of" convenience endpoint is
worth adding on top, without changing the per-conversation cursor
semantics underneath it.

**Reversal cost:** High. Switching to a single global per-user cursor
after clients have integrated against `(target_type, target_id, since)` is
a breaking wire-contract change (query params, WS frame shape) that also
needs a new server-side aggregation model — a unified ordered feed across
circles and DMs, not a schema tweak. Every client built against
per-conversation `GET /messages` calls, and any stored per-conversation
cursor state, would need a migration path. Infrastructural, same class as
decision #3 — which is why the bulk-endpoint question stays in
`OPEN_QUESTIONS.md` rather than getting settled here.

**Storage-layer footnote:** "per-conversation" here was written before it
was checked against a two-party DM specifically — `target_id` alone names
a circle fine but not a DM (see the storage note on `README.md`'s decision
#3 and `docs/SCHEMA_DRAFT.md` design question #1a). Resolved storage-side
with a server-derived `conversation_id`; nothing above changes as a
result.

## 8. Enum, not a bare string, for every closed set

`TargetType`, `MessageKind`, `MessageStatus`, `FrameType`, `ErrorCode`, and
`MembershipRole` are all closed Pydantic enums. Same reasoning as
`services/ai/DECISIONS.md` #2: a typo fails at the contract boundary
instead of silently mis-routing a message downstream. `source_lang` is the
one deliberate exception — see decision #5 above for why it stays a free
string instead of reusing `contracts.ai.language.LanguageCode`.

**Reversal cost:** Low to loosen, medium to re-tighten. Enum → free string
is backward compatible (every valid enum value is still a valid string, no
consumer breaks) so dropping the enum later is cheap. Going the other way
— after arbitrary strings have already been sent and stored — is not:
whatever violates the closed set would need cleanup before the enum could
be reimposed without breaking existing data.

## 9. `contract_version` lives on top-level payloads only

`VersionedModel` is the base for every request/response/frame-data model,
but not for nested value objects (`MediaRef`). Same reasoning as
`services/ai/DECISIONS.md` #5 — a `MediaRef` embedded inside a `MessageIn`
is never validated or transmitted standalone, so its own version stamp
would be noise.

**Reversal cost:** Low. Adding `contract_version` to `MediaRef` later is
additive — give it a default and old payloads without the field still
parse. Mechanical model change, no route or behavior change required.

## 10. `kind`/payload consistency is enforced by the model, not left to callers

`MessageIn` rejects `kind: "text"` with no `text`, and `kind: "voice"` with
no `media_ref`, via a `model_validator`. This was chosen over documenting
the constraint and trusting callers to honor it, so a malformed request
fails at the contract boundary (a 422 from the mock) instead of reaching
the service layer with an ambiguous "voice message with no audio" state to
handle.

**Reversal cost:** Low to change the code, but not free. Deleting the
`model_validator` is a one-line change, but it silently reopens exactly
the ambiguity — a voice message with no `media_ref` reaching the service
layer — that this decision exists to keep out. The cost isn't in the
diff, it's in reintroducing the bug class the validator was written to
prevent.

## 11. `CircleCreate`/`MembershipCreate` are separate models from `Circle`/`Membership`

Server-assigned fields (`id`, `created_by`, `created_at`, `circle_id`,
`joined_at`) are absent from the `*Create` request models entirely, rather
than present-but-ignored-if-sent. A client cannot construct a request that
claims a `created_by` or `id` — those come from the authenticated caller
and the URL path respectively, not the body, so they can't be spoofed by
sending a plausible-looking value.

**Reversal cost:** Medium. Merging the `*Create` models back into
`Circle`/`Membership` (present-but-ignored-if-sent) reopens the spoofing
surface this decision closed — every route handler that currently trusts
"if it's on the `*Create` model, it's not attacker-controlled" would need
re-auditing, not just a type merge.

## 12. No new root-level packaging files

Consistent with `services/ai/DECISIONS.md` #11: no root `pyproject.toml`,
`pytest.ini`, or `.gitignore` was added. `contracts/` remains a PEP 420
namespace package (no `__init__.py` at that level); `contracts/chat/`
itself has one (empty), matching `contracts/ai/__init__.py`. All new
tooling (fixture generator, tests) lives inside `services/gateway/`, which
already owned the only `pyproject.toml` this task's scope allowed touching.

**Reversal cost:** Low. Adding `contracts/chat/pyproject.toml` later, if
the mock ever needs to be pip-installable standalone, is additive — it
doesn't require undoing anything, since tests and tooling already work
from `services/gateway/` today.
