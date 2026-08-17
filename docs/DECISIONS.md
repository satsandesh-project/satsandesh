# Repo-wide notes

## Two workstreams, one person, two project documents

This repository intentionally contains two separate workstreams, both belonging
to the same person (M3), built under two different planning documents:

- **`services/ai/`** (+ `contracts/ai/`) — built under the original project
  proposal's **Student 3: Speech & Language AI** role (ASR, MT, TTS, moderation,
  GPU serving). Under the Month 1 schedule, this work is scheduled for **Month 2**,
  not Week 1 — it was built ahead of that schedule. It is a complete, tested
  deliverable (contracts, mock server, golden fixtures, docs), not abandoned or
  misplaced work from a different teammate.
- **`services/gateway/`** — M3's actual **Month 1, Week 1** task under the
  compressed schedule: the FastAPI gateway skeleton. This is the active
  workstream going forward.

Recorded here so that neither folder is later mistaken for orphaned or
stray work — including by M3 in a future session, which is exactly the
confusion this note exists to prevent.

**Future shape:** `services/gateway/` is expected to eventually proxy to
`services/ai/`, using the Pydantic contracts defined in `contracts/ai/`. See
[`services/ai/README.md`](../services/ai/README.md) for the contract API
reference and [`services/ai/DECISIONS.md`](../services/ai/DECISIONS.md) for the
design rationale behind those contracts specifically.

## Dependency pinning diverges between the two services — kept deliberately

`services/gateway/` pins exact versions in `pyproject.toml` and additionally
keeps a fully-pinned `requirements.txt` (direct + transitive) as a lock file.
`services/ai/` uses floating lower-bound ranges (`fastapi>=0.110`) and has no
`requirements.txt` at all. This is a real inconsistency between the two
services, kept intentionally rather than reconciled:

- The gateway is going to be containerized by a teammate, and Dockerfiles
  install from `requirements.txt` — that needs to be a reproducible, fully
  resolved set of versions, not a range pip re-resolves at image-build time.
- The gateway depends on `python-jose[cryptography]`, a security-sensitive
  package where a floating range is a real risk (a transitive resolution
  change landing in a container build without anyone reviewing it). `services/ai/`
  has no comparably sensitive dependency today.

**Open question for the team, not a decision made here:** should `services/ai/`
converge on the same exact-pin + `requirements.txt` approach once it's also
containerized, for consistency across services? Not changed as part of this
work — `services/ai/`'s `pyproject.toml` and lack of `requirements.txt` are
untouched.

## `/ws` rejects a missing/invalid token by accept()-then-close(1008), not a pre-accept close

`app/ws.py`'s `/ws` route authenticates via a `?token=` query param (browsers
can't set custom headers on a WS handshake). The natural-looking implementation
— reject by calling `close(1008)` before `accept()` — turned out to be
observably broken in a real browser, not just inelegant: uvicorn never
completes the WS opening handshake in that case, so it can't send a real close
frame at all. It collapses the rejection to a flat HTTP 403 and discards
whatever close code the app asked for. A real browser then reports that
handshake failure as close code **1006** (abnormal closure) — indistinguishable
from a dead network. `TestClient`, which reads the raw ASGI message stream
in-process rather than going over the wire, faithfully echoed back the `1008`
we asked for, so the test suite passed while disagreeing with what a browser
actually observed. Caught by manual browser verification, not by pytest.

**Decision:** `accept()` the handshake first, then immediately `close(code=1008,
reason="missing_or_invalid_token")`, before `manager.connect()` and before the
receive loop — so no connection is ever registered and no data is ever read
from the socket. This sends a genuine WS close frame with the real code and
reason intact, which a browser can observe in `onclose`.

**Security trade-off, made deliberately:** this means the gateway briefly
completes a handshake with an unauthenticated peer before closing on it. The
peer never reaches `ConnectionManager` and the server never reads a byte from
it, so the exposure is a completed-then-immediately-terminated handshake, not
an open connection. Rate limiting the `/ws` route is the real defense against
someone flooding handshakes to burn server resources, and is explicitly **not**
in scope for Week 1 — noted here so it isn't forgotten, not solved here.

**Why this matters beyond code cleanliness:** Week 3's task is offline queue
and reconnect logic for elders on unreliable rural mobile networks. Reconnect
logic needs to be able to tell these two failure modes apart:

- **1008** (this decision) — the token was missing or invalid. Stop retrying;
  go re-authenticate.
- **1006** — the network dropped the connection before/during handshake.
  Keep retrying with backoff.

Collapsing both to 1006 (the pre-accept-close behavior) would make a client
with a bad token retry forever against a server that will never accept it.

## Chat message `id` is UUIDv7, not random UUIDv4 — settled ahead of the Week 2 schema

`contracts/chat/`'s wire contract left the server-assigned message `id`
typed as an opaque `str` (`contracts/chat/DECISIONS.md` #3), deliberately
not constraining *which* id scheme a real implementation would use. That
part is now settled, ahead of Week 2's data model and Week 3's sync work,
by M3 (who owns Week 2): the scheme is a time-sortable id (UUIDv7), not a
random UUIDv4.

**Why this belongs in the repo-wide notes, not just `contracts/chat/`:**
the choice isn't cosmetic — it determines the *shape* of the Week 3 sync
cursor. A random UUIDv4 carries no ordering information, so sync has to
page on `(created_at, id)` (a composite cursor, a three-column index, and
a real correctness hazard from clock skew and identical timestamps across
processes). A time-sortable id lets sync page on `id` alone (a scalar
cursor, a two-column index, no separate tiebreaker field). Full comparison
and reasoning: `contracts/chat/DECISIONS.md` #3.

**Decision:** Week 2's `id` column is UUIDv7, generated server-side at
insert time. Week 3's sync cursor (`since=`) is the message `id` itself —
no `created_at` field gets added to the sync contract to cover ordering
gaps, because a UUIDv7-keyed schema doesn't have any.

**Consequence for both workstreams:** Week 2's schema and Week 3's sync
logic need to be built against the same assumption from the start —
`created_at` is a display field, not a sort key. Recorded here so a
teammate picking up either piece of work doesn't have to reconstruct the
coupling between "which id scheme" and "what a cursor can be" from first
principles, and doesn't accidentally design Week 2's schema and Week 3's
sync against two different assumptions about how ordering works.

**Reversal cost:** High once real data exists — see
`contracts/chat/DECISIONS.md` #3 for why. That's the reason this was
decided now, before Week 2's schema and before any client holds a sync
cursor, rather than left open.
