# SatSandesh Chat Contracts (`contracts/chat/`)

Consumer-facing reference for the chat wire shapes — HTTP routes, the
WebSocket frame envelope, and the mock gateway other members build against
today. Sibling package to `contracts/ai/`; read `services/ai/README.md` and
`services/ai/mock/app.py` first if you haven't — this package deliberately
mirrors their conventions (golden fixtures, mock latency headers,
`CONTRACTS_VERSION`, `DECISIONS.md`/`OPEN_QUESTIONS.md`) so the two contract
folders read as siblings, not as two different projects.

Contract shape version: `CONTRACTS_VERSION = "0.1.0"`
(`contracts/chat/common.py`). Every request/response/frame-data payload
carries `contract_version` so you can tell which shape you're looking at as
this evolves week to week. Independent of `contracts/ai/`'s version counter
— see `DECISIONS.md`.

## Quickstart: running the mock gateway

```bash
cd services/gateway
./.venv/Scripts/python.exe -m pip install -e .[dev]
./.venv/Scripts/python.exe -m uvicorn contracts.chat.mock.app:app --reload --port 8002
```

Interactive docs at `http://localhost:8002/docs`. Every response is a real
instance of the Pydantic model below — nothing is hand-typed JSON.

**Note the module path:** the mock lives at `contracts/chat/mock/app.py`,
*inside* the contracts package, not under `services/gateway/mock/` the way
`services/ai/mock/app.py` sits under its owning service. That's a
deliberate deviation from the `contracts/ai/` + `services/ai/` split — see
`DECISIONS.md` for why.

Identity and latency are both mock-only concerns, standing in for real auth
and a real AI/moderation pipeline:

- `X-Mock-User-Id` header (default `mock-user-1`) — who a `POST /messages`
  or `POST /circles` call is "from." The real gateway will derive this from
  the auth token instead (see `services/gateway/app/auth.py`).
- `X-Mock-Latency-Ms` header, or the `MOCK_LATENCY_MS` env var (default
  `0`) — artificial per-request delay, same mechanism as
  `services/ai/mock/app.py`, so you can develop against realistic timing.
- WebSocket `/ws?user_id=...` — same idea over the WS transport, mirroring
  how `services/gateway/app/ws.py`'s real `/ws` reads a token from a query
  param because browsers can't set custom headers on a WS handshake.

State is in-memory and resets on restart — no database, same as
`services/ai/mock/app.py`.

## Design decisions

These were explicit choices, not silent defaults — flagged here as
candidates for "why did you do it this way" in review, same spirit as
`services/ai/DECISIONS.md`. Full rationale for everything else (including
smaller ones) is in `contracts/chat/DECISIONS.md`.

### 1. Polymorphic `target_type` + `target_id`, not separate nullable columns

A message (and a sync cursor, and a WS sync frame) is addressed with
`target_type: "user" | "circle"` plus a single opaque `target_id`, instead
of two nullable fields (`target_user_id`, `target_circle_id`) where exactly
one is populated.

**Why:** a single discriminated pair means every piece of code that
branches on "who is this message going to" checks one explicit field
(`target_type`) instead of inferring it from which of two IDs happens to be
non-null. It also keeps `MessageIn`/`MessageOut`/`SyncRequest`/`SyncBatch`
structurally identical regardless of whether the target is a DM or a
circle — one shape, not two shapes unioned together.

**Trade-off:** the wire contract can't express "target_id must be a valid
circle when target_type is circle" — that's a referential-integrity
concern (a DB foreign key, or an app-level lookup) that belongs in
`services/gateway/`, not in a Pydantic model. A typo'd `target_id` fails at
the service layer, not at deserialization time.

### 2. Client generates `client_msg_id`; server generates the authoritative `id`

`MessageIn.client_msg_id` is a client-generated UUID. The server never
trusts it as a display-order identifier — it assigns its own `id`
(returned in `AckOut` and `MessageOut`) as the authoritative one.

**Why:** SatSandesh's stated context is elders on unreliable rural mobile
networks (see `services/gateway/README.md`'s Week 3 reconnect-logic
framing). A client that sends a message, loses connectivity before seeing
an ack, and retries needs a way to say "this is the same send, not a
duplicate" — `client_msg_id` is that idempotency key, generated once when
the user hits send and reused on every retry until an `AckOut` confirms it.
The server's `id`, by contrast, must be safe to use for global ordering and
sync cursors; a client-generated value can't be trusted for that (clock
skew, no coordination between clients, and a malicious or buggy client
could otherwise forge ordering).

`id` is kept as an opaque string, not constrained to UUID — see
`DECISIONS.md` #3 for why.

### 3. Sync cursor is per-conversation, not a single global per-user cursor

`SyncRequest`/`SyncBatch` (both the WS `sync.request`/`sync.batch` frames
and `GET /messages?target_type=&target_id=&since=&limit=`) are keyed by
`(target_type, target_id)`. There is no single global "last message id I've
seen" per user — the cursor is scoped to one conversation at a time.

**Why:** a user is a member of multiple circles and has multiple DMs
simultaneously. A single global cursor can't express "I've read circle A up
through message 50 but haven't opened circle B yet" — it would force every
sync to either replay everything or risk skipping unread messages in a
conversation the client hasn't explicitly synced. Scoping the cursor to the
conversation lets a client resume exactly where it left off, per
conversation, independently.

**Trade-off:** a client with N conversations needing a fresh sync (e.g.
after being offline) issues N sync calls, not one. This is deliberate —
see `OPEN_QUESTIONS.md` for whether a "sync everything since I went
offline" convenience endpoint is worth adding later.

**Storage note (added during `docs/SCHEMA_DRAFT.md` review):** `target_id`
alone can name a circle (every member's messages share the one circle id)
but not a two-party DM — which party is "the other one" flips depending on
who's asking, so keying storage on the sender's own `(target_type,
target_id)` verbatim (as `contracts/chat/mock/app.py` does today) silently
splits a DM into two one-directional streams instead of one conversation.
`docs/SCHEMA_DRAFT.md` design question #1a works this through with
concrete rows and resolves it with a server-side `conversation_id`,
derived from `(target_type, target_id)` plus the authenticated caller
(who's always known from auth, never from the body). This is a storage-
and service-layer resolution only — `SyncRequest`/`SyncBatch`'s shape
above is unchanged, and a client keeps sending exactly the same
`(target_type, target_id)` pair it sends today.

## HTTP routes

All routes require a Bearer token (real auth is `services/gateway/`'s Week
2 work — see its README for the current stub). Base path is unprefixed
here; the gateway may mount this under a version prefix.

| Method | Path | Request model | Response model | Auth required |
|---|---|---|---|---|
| `POST` | `/messages` | `MessageIn` | `AckOut` | Yes |
| `GET` | `/messages?target_type=&target_id=&since=&limit=` | — (query params, shape of `SyncRequest`) | `SyncBatch` | Yes |
| `GET` | `/circles` | — | `list[Circle]` | Yes |
| `POST` | `/circles` | `CircleCreate` | `Circle` | Yes |
| `POST` | `/circles/{id}/members` | `MembershipCreate` | `Membership` | Yes |

`GET /messages` deliberately returns the same `SyncBatch` shape a
`sync.batch` WS frame carries — see design decision #3 — so a client's
merge logic doesn't need two code paths for the two transports.

### `POST /messages` — send a message

Request (`MessageIn`):
```json
{
  "contract_version": "0.1.0",
  "client_msg_id": "8f14e45f-ceea-467e-adde-3fb5d3a5fa1c",
  "target_type": "circle",
  "target_id": "circle-satsang-evening",
  "kind": "text",
  "text": "ఈ రోజు సత్సంగం ఎప్పుడు జరుగుతుంది?",
  "source_lang": "te"
}
```
`kind: "text"` requires `text`; `kind: "voice"` requires `media_ref`
instead — enforced by the model, not left to the caller to remember.
`source_lang` is a free-form BCP-47-shaped string, not
`contracts.ai.language.LanguageCode` — see `DECISIONS.md` #5 for why this
package doesn't import `contracts/ai/`.

Response (`AckOut`):
```json
{
  "contract_version": "0.1.0",
  "client_msg_id": "8f14e45f-ceea-467e-adde-3fb5d3a5fa1c",
  "id": "msg-01H8X5Q7Z1",
  "status": "pending"
}
```

### `GET /messages?target_type=&target_id=&since=&limit=` — sync a conversation

`since` is exclusive: the last `id` the client already has, or omitted for
full history. `limit` defaults to 50, capped at 200.

Response (`SyncBatch`):
```json
{
  "contract_version": "0.1.0",
  "target_type": "circle",
  "target_id": "circle-satsang-evening",
  "messages": [
    {
      "contract_version": "0.1.0",
      "id": "msg-01H8X5Q7Z1",
      "author_id": "user-elder-42",
      "target_type": "circle",
      "target_id": "circle-satsang-evening",
      "kind": "voice",
      "text": null,
      "created_at": "2026-08-17T09:00:00Z",
      "status": "pending"
    }
  ],
  "has_more": false
}
```
`text` is `null` on a `voice` message until transcription resolves it — see
`OPEN_QUESTIONS.md` for the open question about `MessageOut` not yet
carrying a `media_ref` for playback.

### `GET /circles` / `POST /circles`

`POST /circles` request (`CircleCreate`):
```json
{ "contract_version": "0.1.0", "name": "Evening Satsang" }
```
Response (`Circle`):
```json
{
  "contract_version": "0.1.0",
  "id": "circle-satsang-evening",
  "name": "Evening Satsang",
  "created_by": "user-moderator-1",
  "created_at": "2026-08-10T18:30:00Z"
}
```
`created_by` comes from the authenticated caller, never the request body —
it can't be spoofed by a client.

### `POST /circles/{id}/members`

Request (`MembershipCreate`):
```json
{ "contract_version": "0.1.0", "user_id": "user-elder-42", "role": "member" }
```
`role` defaults to `member` if omitted. `circle_id` on the response comes
from the URL path, not the body.

Response (`Membership`):
```json
{
  "contract_version": "0.1.0",
  "circle_id": "circle-satsang-evening",
  "user_id": "user-elder-42",
  "role": "member",
  "joined_at": "2026-08-11T07:15:00Z"
}
```

## WebSocket envelope

Every frame is `{"type": ..., "data": {...}}` (`contracts/chat/envelope.py`).
`FrameType` is closed:

| `type` | `data` shape | Direction |
|---|---|---|
| `message.send` | `MessageIn` | client → server |
| `message.ack` | `AckOut` | server → client, reply to `message.send` |
| `message.new` | `MessageOut` | server → client, a message arriving |
| `sync.request` | `SyncRequest` | client → server |
| `sync.batch` | `SyncBatch` | server → client, reply to `sync.request` |
| `error` | `ErrorPayload` | server → client |

Parse an inbound frame as `RawFrame` first (validates `type`, leaves `data`
as a plain `dict`), then re-validate `data` against the model the `type`
implies. The parametrized aliases (`MessageSendFrame = Frame[MessageIn]`,
etc.) exist for the case where you already know the type and want one-step
validation.

Example `message.send` → `message.ack` round trip:
```json
{"type": "message.send", "data": { "...": "MessageIn, see above" }}
```
```json
{"type": "message.ack", "data": { "...": "AckOut, see above" }}
```

## Message status

`MessageStatus` (`contracts/chat/common.py`) is the chat-level delivery
lifecycle, not a moderation label:

| Value | Meaning |
|---|---|
| `pending` | Accepted by the server, still moving through the pipeline (transcription, translation, moderation). |
| `delivered` | Visible to the target audience. |
| `held` | Parked pending moderator review. |
| `blocked` | Rejected by moderation; not delivered. |
| `failed` | Pipeline error before delivery. |

This is deliberately a different enum from
`contracts.ai.moderation.ModerationAction` (`ALLOW`/`NUDGE`/`HOLD`/`BLOCK`)
— see `DECISIONS.md` #6.

## Golden fixtures

`services/gateway/tests/fixtures/chat/*.json` — one canonical example per
model above, checked in and asserted
(`services/gateway/tests/test_golden_fixtures_chat.py`) to still parse into
its model. Regenerate after a deliberate contract change:
```bash
cd services/gateway
PYTHONPATH=../.. ./.venv/Scripts/python.exe tools/generate_chat_fixtures.py
```

## Development

```bash
cd services/gateway
./.venv/Scripts/python.exe -m pytest tests/ -v
./.venv/Scripts/python.exe -m ruff check ../../contracts/chat app tests tools
./.venv/Scripts/python.exe -m ruff format ../../contracts/chat app tests tools
```

Tests for this package live in `services/gateway/tests/` (prefixed
`test_contracts_chat_*`, `test_golden_fixtures_chat.py`,
`test_mock_chat_server.py`), not inside `contracts/chat/` itself — see
`DECISIONS.md` for why, and `services/ai/README.md` for the sibling
pattern this follows.

See `DECISIONS.md` for full design rationale and `OPEN_QUESTIONS.md` for
what still needs the team's sign-off.
