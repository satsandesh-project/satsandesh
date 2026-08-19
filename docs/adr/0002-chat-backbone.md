# ADR 0002: Chat Backbone — Matrix (Tuwunel) vs Custom-Lite

**Status:** Proposed (spike in progress)
**Date:** 2026-08-01
**Author(s):** Student 2

> ### ⚠ Note — 2026-08-19: decision still open, and now past its time-box
>
> This ADR's own spike plan set a two-week box with a day-10 pivot point.
> That box has now passed and **the decision has still not been made**:
> Spike B's findings are recorded below, but Spike A (Matrix/Tuwunel) has
> not reported, so there is nothing to compare against yet.
>
> **Week 3 work (circles + announcement channels) is proceeding anyway,
> deliberately built behind an abstract interface** (`backbone/interfaces.py`)
> rather than waiting for this decision or assuming its outcome. Circles are
> needed by other students' work now, and blocking them on an unresolved
> architecture decision would be the worse trade.
>
> **The concrete implementation wired up for now is Option B (custom-lite)**
> — for the plain reason that it is the only option that currently exists in
> working form. **This is provisional and is NOT a de facto decision.** It
> was chosen for availability, not on merit, and no comparison has been run.
> Whoever picks this up must revisit it once Spike A's findings land; the
> interface exists precisely so that swapping the implementation is a
> contained change rather than a rewrite.
>
> Treating "Option B is what's currently wired up" as "Option B won" would be
> exactly the mistake this note exists to prevent.

## Context

The proposal's original architecture (Section 8) names two options for the
chat backbone:

- **Option A:** Matrix homeserver (originally specified as Conduit) with an
  Application-Service bot intercepting events.
- **Option B:** Custom-lite backbone — FastAPI + WebSockets + a PostgreSQL
  outbox.

This ADR records the two-week spike comparing them, updated with current
(August 2026) research, since the Matrix homeserver ecosystem has moved
since the proposal was written.

## Update: the "Conduit" landscape has shifted

The proposal names Conduit specifically. As of this research:

- **Conduit** (original) is still in beta; its own docs now recommend a fork
  for production use.
- **conduwuit**, the fork that had become the de facto production choice,
  was **archived by its owner in January 2026** — no longer maintained.
- **Tuwunel** is the official successor to conduwuit, written in Rust,
  positioned as enterprise-ready, and notably used in production by the
  Swiss government for citizen services — a strong maintenance signal.
- **Continuwuity** is a separate community-driven continuation of
  conduwuit/Conduit — a second active fork.

**Implication:** if we pursue Option A, we should spike on **Tuwunel**, not
literally "Conduit" as named in the original proposal. It reads conduwuit's
database format directly, so it's a drop-in successor wherever conduwuit
was assumed.

## New consideration: E2EE and application services

Research surfaced a real constraint that applies to Option A regardless of
which Matrix homeserver we pick: **application-service bots and full
end-to-end encryption don't mix well.** An appservice bot needs to read
message content directly to run it through translation and moderation —
but if E2EE is enabled, encrypted content isn't readable server-side by
design (that's the point of E2EE), and bridging bots have documented
crashes/incompatibilities when E2EE is layered on top.

**This means:** regardless of Option A vs B, our architecture requires
messages to be readable server-side for the AI pipeline to work. This is
worth stating explicitly and confirming the team/supervisor are aligned on
it — full E2EE (a common assumption for "private messaging apps") is not
compatible with server-side moderation and translation as designed. This
should go in the privacy/ethics section of the SRS, not just buried here.

## Options considered

### Option A: Matrix (Tuwunel) + Application-Service bot
**Pros:**
- Delivery semantics (retries, ordering), offline sync, receipts, and a
  media repo come for free — we don't re-implement them.
- Federation-ready if the org ever wants interop with other Matrix servers
  (not a stated requirement, but free optionality).
- Tuwunel specifically: actively maintained, Rust (fast, low resource use),
  runs well on modest hardware — good fit for a small self-hosted deployment.

**Cons:**
- Real learning curve: Matrix's event model, application-service
  registration, and admin APIs are unfamiliar to a beginner team.
- Appservice + E2EE friction (see above) — need to explicitly run without
  E2EE, which needs to be a documented, deliberate decision.
- Fewer people. Debugging help from AI coding assistants may be less
  reliable for Matrix-specific issues than for plain FastAPI, since Matrix
  has a smaller/more specialized surface than mainstream web frameworks.

### Option B: Custom-lite — FastAPI + WebSockets + PostgreSQL outbox
**Pros:**
- Simpler mental model: it's "just" a web app, using patterns the team is
  more likely to already understand or that AI assistants handle very well
  (FastAPI is extremely common in training data/documentation).
- Full control — no fighting a framework's assumptions about federation,
  encryption, or room semantics we don't need.
- No server-side E2EE conflict to reason about — we're not pretending to
  offer something (E2EE) we can't actually deliver anyway, given the AI
  pipeline requirement.

**Cons:**
- We build delivery guarantees, retry logic, and offline sync ourselves —
  real distributed-systems work, done by a beginner team, that Matrix
  would otherwise hand us.
- More surface area for subtle bugs (message ordering, duplicate delivery,
  missed messages on reconnect) that a mature protocol has already solved.

## Decision

**Still not made — this is Spike B's findings only.** Per the spike plan
below, Student 2 ran Spike B (custom-lite) this week while a teammate runs
Spike A (Matrix/Tuwunel) in parallel. This ADR will move to Accepted once
both spikes' findings are compared; until then, Status stays Proposed.
What follows is Option B's half of that comparison — including the case
against it, since Student 2 built it and is the obvious person to be
biased in its favor.

## Spike plan (hands-on portion — pending Docker being unblocked locally)

1. Stand up Tuwunel via Docker, confirm it starts and a client (e.g.
   Element) can register and send a message.
2. Register a minimal Application Service against it — confirm the bot can
   receive an event and inject a reply.
3. Explicitly disable/avoid E2EE for the relevant rooms and confirm the
   bot can read plaintext content.
4. Time-box: if steps 1-3 aren't working smoothly by roughly the
   day-10 mark, stop and pivot to Option B.
5. Document actual findings here (replacing this "pending" section) and
   move Status to Accepted.

## Spike B findings — custom-lite (FastAPI WebSockets + Postgres outbox)

Code: `backbone/spike-custom-lite/`. Runs via
`docker compose --profile spike up` (never with a plain `docker compose
up` — Week 1's stack is untouched by this). Full detail, including every
dead end and how each was found, is logged as this week's entry in
`docs/prompt-journal.md`; this section is the distilled result.

### What was built

The outbox pattern, minimally: `POST /send` writes a message row and one
outbox row per recipient in a single transaction, so the delivery
*obligation* is durable the moment that commits — independent of whether
anything else survives. A background dispatcher polls
`spike_outbox` with `SELECT ... WHERE status='pending' ORDER BY id FOR
UPDATE SKIP LOCKED`, pushes to whichever recipients are currently
connected (an in-memory `user_id -> socket` registry), and marks
delivered — or leaves pending, incrementing `attempts`, if the recipient
is offline.

### The five required behaviours — all demonstrated, with real numbers

| # | Behaviour | Result |
|---|---|---|
| 1 | Online delivery | Pass. Recipient connected, message arrives immediately. |
| 2 | Offline queueing + ordered reconnect drain | Pass. 3 messages sent while recipient never connected; all 3 arrive in original send order on connect. |
| 3 | Ordering under a burst | Pass. 20 rapid messages arrive in exact send order — guaranteed by construction (`ORDER BY id` on every claim), confirmed in practice. |
| 4 | Crash safety | Pass, with real numbers: 300 messages seeded, dispatcher hard-killed (`SIGKILL`-equivalent, no graceful shutdown) mid-batch, restarted. **0 lost. 9–10 duplicates** (varies run to run — see below). |
| 5 | Concurrency (two dispatchers, no double-delivery) | Pass, with genuine contention forced: 40 rows seeded, two claimers capped at 25 rows each so neither could grab everything — split 25/15, **zero overlap**. |

Behaviour 4's duplicate count needed real engineering to even measure
honestly. The first attempt at this test proved nothing: 15 messages,
all delivered comfortably before the kill landed (0 lost, but nothing
about *interruption* was tested). Scaling to 300 messages made the kill
land exactly on a batch boundary — 50 delivered cleanly, 250 recovered,
still 0 duplicates, because Postgres transactions are all-or-nothing: an
interrupted 50-row batch either fully commits or fully rolls back, there
is no partial-batch outcome to catch by accident. Proving the actual
duplicate-window risk required a test-only fault-injection knob
(`SPIKE_DELIVERY_DELAY_MS`, zero effect unless explicitly set — see
`dispatcher.py`) to slow delivery enough that an external kill could
reliably land *inside* an open transaction rather than before or after
it. Once that landed, the exact mechanism held up empirically: messages
pushed to the socket before the crash, whose "mark delivered" `UPDATE`
never committed, come back again after restart. **At-least-once, not
exactly-once — confirmed, not just reasoned about.**

### What duplicates would cost to fix

This spike does not deduplicate. Doing so would need one of:
- A client-generated idempotency key per send, deduplicated on the
  `spike_outbox` insert (cheap, but only protects against retried
  *sends* — doesn't touch the delivery-side duplicate window above).
- A delivery-side dedup table (`delivered_message_ids` per recipient, or
  a `UNIQUE` constraint checked before pushing) — closes the real gap,
  at the cost of a lookup on every delivery attempt and yet another
  piece of state to keep consistent.
- Client-side dedup by message ID before rendering — pushes the cost to
  every client instead of the server, which is where Reflex's PWA client
  (Student 1's side) would need to absorb it.

None of these are hard individually. All of them are scope this spike
does not have yet, on top of everything else in "what's missing" below.

### What's missing for this to be a real backbone (cost over the remaining 2 months)

- **No auth.** `user_id` is a trusted query param. Real auth (and
  figuring out how it interacts with the gateway, per `gateway/README.md`
  — auth is explicitly gateway's job) is unstarted.
- **No connection pooling.** Every dispatcher poll cycle opens a fresh
  Postgres connection. Measured idle cost: **~42MB RAM, ~3% CPU with zero
  messages in flight** (`docker stats`, spike-backbone container) — most
  of that CPU is the connect/disconnect churn, not useful work. A pooled
  connection or switching the poll loop to Postgres `LISTEN`/`NOTIFY`
  (push instead of poll) would likely drop idle CPU close to zero, but is
  unbuilt.
- **No migration tooling.** `db.ensure_schema()` runs `CREATE TABLE IF
  NOT EXISTS` on every startup. Fine for a spike; not fine once the
  schema needs to actually evolve under real data.
- **No message history / pagination API.** Only live delivery exists —
  nothing serves "load the last 50 messages in this conversation."
  Sizeable, unstarted feature.
- **No dedup**, as above.
- **Registry is single-process.** A second app instance can't see the
  first instance's connected recipients — that row just sits pending
  until the *first* instance's dispatcher (the one actually holding that
  socket) claims it. Horizontal scaling needs shared registry state (e.g.
  Redis pub/sub) that doesn't exist yet. Not a blocker at pilot scale
  (a handful of concurrent users), but a real wall if the pilot grows.
- **`WindowsSelectorEventLoopPolicy` is deprecated, slated for removal in
  Python 3.16.** Irrelevant if we deploy on Linux (where none of this
  Windows-specific event-loop fighting applies at all — see the prompt
  journal), but worth flagging: this whole class of problem was purely a
  local Windows dev-machine issue, not something a Linux deployment target
  would hit.

Rough sense of scale: what exists today is a convincing proof of the
*durability mechanism*, not a chat backbone. Auth, dedup, history/
pagination, and pooling are each independently non-trivial; together
they're a large fraction of "build a chat backbone from scratch," which
is exactly the "real distributed-systems work... done by a beginner
team" cost already named in Option B's Cons above. The spike answered
"does the mechanism work" (yes) without answering "is building the rest
of it a good use of a 4-person student team's remaining 7 months"
(separate question).

### Decision criteria — filled in

| Criterion | Option A (Matrix/Tuwunel) | Option B (this spike) |
|---|---|---|
| Time to first working [mechanism] | *Pending teammate's Spike A* | **~20 minutes** from first line of code to all 5 behaviours passing (excludes ~25 min of unrelated local Docker Desktop environment troubleshooting the same session — see prompt journal). Extremely fast for *this narrow mechanism*; says nothing about auth/dedup/history, which are unbuilt. |
| Can it run without E2EE cleanly | *Pending* | N/A in the same sense the question is asked for Option A — there's no E2EE layer to disable in a system that never had one. Messages are plaintext in Postgres by construction, which is the E2EE-vs-moderation tension ADR 0002 already flags — just arrived at by never building encryption, not by deliberately turning it off. |
| Resource use (RAM/CPU, idle) | *Pending* | **~42MB RAM, ~3% CPU idle** (measured, `docker stats`, zero messages in flight). CPU is inflated by no-pooling poll churn — see above. |
| Team comprehension | *Pending — needs the whole team, not one spike* | Not honestly measurable from a solo spike. Subjectively: the mechanism (write + poll + push) is a small number of ideas, but `SKIP LOCKED` concurrency semantics and the crash-safety duplicate window are genuinely non-trivial to reason about correctly — this ADR's author needed three iterations of the crash-safety test design before it actually proved what it claimed to. |
| AI-assistant helpfulness | *Pending* | Mixed, honestly. Schema design, the outbox pattern itself, and the `SKIP LOCKED` concurrency logic were correct on the first pass — no bugs found there in testing. But a real, non-hallucinated platform-compatibility miss cost real time: psycopg's async mode doesn't work with Windows' default event loop, and the obvious fix (set a global event loop policy) turned out not to work with this uvicorn version either, because uvicorn 0.52 resolves its own loop via a newer `loop_factory=` mechanism that bypasses the global policy entirely — found only by reading uvicorn's source, not by asking Claude Code about it first. No hallucinated APIs observed anywhere; the failures were real platform-interaction gaps, not invented methods. |

### Recommendation (and the case against it)

**Recommendation: Option B is viable as a mechanism, and building it
here should not by itself decide the ADR.** The core claim under test —
"can a beginner team get durable, ordered, crash-safe delivery out of
Postgres without a mature protocol underneath" — held up under honest
testing this week, including a genuine, reproduced duplicate-delivery
event under simulated crash. That's a real result, not a hunch.

**The case against it, stated plainly:** everything that made this spike
fast (~20 minutes to 5 passing behaviours) is exactly the part Option A
gets for free and Option B does not. This spike proved the *outbox*
mechanism works; it did not build auth, message history, dedup, or
multi-instance scaling — each independently real work, and together
comparable in scope to what a Matrix homeserver already provides.
Extrapolating "20 minutes for the durability core" into "therefore the
whole backbone is cheap" would be the wrong lesson to draw from this
spike, and this ADR's author (having built Option B, and so having some
motivation to like it) flags that explicitly rather than let the fast
spike time speak for itself.

The honest comparison this ADR needs is not "which spike went faster" —
it's "which set of unbuilt-but-required pieces is smaller, for a
4-person team with 7 months left." That comparison needs Spike A's
findings, not just this one. **Decision stays Proposed until both are in.**

## Consequences

Whichever option is chosen becomes the foundation every other student's
service depends on (AI services read/write through it, moderation acts on
it, live audio may share auth with it). Getting this decision right early
matters more than getting it fast — hence the deliberate two-week box
rather than picking on day one.
