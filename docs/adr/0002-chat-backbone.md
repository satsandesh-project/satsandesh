# ADR 0002: Chat Backbone — Matrix (Tuwunel) vs Custom-Lite

**Status:** Proposed (both spikes complete — final decision is the team's, not either spike author's)
**Date:** 2026-08-01 (opened, Spike B) / 2026-08-28 (Spike A findings added)
**Author(s):** Student 2 (Option B / Spike B); Kshitiz Pratap Singh (Option A / Spike A)

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

*Spike A confirms this implication directly, not just as research: Conduit
was tested first (matching the literal proposal) and has a real, reproduced
bug that blocks the architecture from working at all. Tuwunel does not have
this bug — see Spike A findings below.*

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

*Spike A confirms this empirically, not just as a documented risk: an
encrypted test room delivered an unreadable `m.room.encrypted` event to
the bot; a room with encryption explicitly disabled delivered a fully
readable plaintext message. See Spike A findings below.*

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

**Both spikes are now complete.** Spike B (custom-lite) findings and Spike
A (Matrix/Tuwunel) findings are both below, followed by a joined
comparison. **Status remains Proposed** — moving this to Accepted is the
whole team's decision to make together, not something either spike author
decides unilaterally by writing up their own results.

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

### Recommendation (Spike B, and the case against it)

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
findings, not just this one.

## Spike A findings — Matrix (Tuwunel) Application Service

Code: `services/backbone-spike-a/`. Full evidence (raw logs, JSON,
request/response pairs — not paraphrased) is in
`docs/SPIKE_A_FINDINGS.md`; this section is the distilled result, same
role as Spike B's section above.

### What was tested

Whether a Matrix homeserver actually forwards room events to an external
Application-Service bot — the exact mechanism the proposal's
moderation/translation architecture depends on — and whether the bot can
then read the plaintext content it would need to act on.

### Result: the mechanism works, on the right target server

- **Transaction-push mechanism**: confirmed working. A homeserver pushes
  room events to the bot's HTTP endpoint with no polling required, correct
  auth, correct payload.
- **Tested against Conduit first** (the literal proposal), not Tuwunel
  immediately — this surfaced a real, reproduced bug: the bot can never
  successfully *join* a room it's invited to, because Conduit routes every
  room join through a federation code path even for a room 100% local to
  itself, which then fails with no server to consult. Confirmed via direct
  server logs, not inferred.
- **Retested on Tuwunel** — the fork this ADR already recommends. The
  identical join that failed on Conduit succeeds cleanly. This confirms
  the bug is Conduit-specific, not a limitation of the Application Service
  model itself.
- **Full end-to-end proof, twice independently**: invite → bot auto-joins
  → plaintext message delivered to the bot, exact text intact. First
  reproduced on a local dev machine, then reproduced again from scratch
  on the team's shared Linux server (`~/kshitiz/satsandesh/` there),
  confirming it isn't a one-machine fluke.
- **E2EE constraint confirmed empirically**, not just as a documented
  risk: an encrypted test room delivered an unreadable `m.room.encrypted`
  event; a room with encryption explicitly disabled delivered a fully
  readable `m.room.message` with the exact typed body.

### What's missing / not yet answered

- **Team-wide comprehension is untested** — this was a solo spike; how a
  four-person, largely-beginner team finds Matrix's event model day to
  day is a separate, whole-team question (same caveat this ADR already
  notes for Option A generally).
- **No load testing** — a handful of local test accounts, not simulated
  concurrent users.
- **Resource usage (RAM/CPU) was not measured** for Option A the way
  Spike B measured its own (~42MB RAM / ~3% CPU idle) — a fair, specific
  gap to close before this ADR is finalized.
- **Federation was deliberately not exercised** — not a stated product
  requirement.

### Decision criteria — filled in

| Criterion | Option A (Matrix/Tuwunel) | Option B (Spike B, above) |
|---|---|---|
| Time to first working mechanism | Several hours across sessions; most of that was environment recreation (Docker/Python not on `PATH`, a container rebuilt from scratch) and precisely diagnosing the Conduit join bug — not the mechanism itself, which took under an hour once the environment worked. | **~20 minutes** from first line of code to all 5 behaviours passing. |
| Can it run without E2EE cleanly | **Yes — confirmed by direct test.** A room created with encryption off delivered a full plaintext message to the bot. | N/A by construction — no encryption layer was ever built to disable. |
| Resource use (RAM/CPU, idle) | Not measured — open gap, see above. | **~42MB RAM, ~3% CPU idle** (measured). |
| Team comprehension | Pending — needs the whole team, not one spike. Real learning curve confirmed hands-on (non-obvious AS registration mechanics, homeserver-specific admin commands). | Pending — needs the whole team, not one spike. Subjectively small number of ideas, but `SKIP LOCKED` concurrency and the crash-safety duplicate window are genuinely non-trivial (per Spike B's own account). |
| What's delivered by the platform vs. built by hand | Retries, delivery ordering, offline sync, receipts, and a media repository come from the Matrix protocol/server itself — not spike-authored code. | None of the above exist yet; explicitly listed as unstarted in Spike B's own findings (no auth, no dedup, no pooling, no history/pagination, single-process registry). |
| Concrete blocking bugs found | One — an archived-Conduit local-join routing bug — confirmed **absent** on Tuwunel, the actual recommended target. | None reported as blocking; a duplicate-delivery window under crash is real and reproduced, but treated as an accepted, unresolved design tradeoff (at-least-once delivery), not a bug to fix. |

### Recommendation (Spike A)

The AS mechanism is real and does what the proposal needs: a homeserver
will push room events to an external bot with no polling required, the
bot can join rooms it's invited to and read plaintext content, and this
was proven end-to-end on the actual recommended target (Tuwunel), not
just in theory. That part of Option A checks out completely.

What to flag before committing to it: (1) don't build on archived Conduit
for anything beyond another spike — the join bug found here is a
concrete, reproduced reason, not a theoretical one; (2) budget real time
for the "bot must join every room it moderates" step specifically, since
it has its own auth/provisioning/failure surface the original proposal
doesn't call out; (3) resource usage for Option A hasn't been measured
the way Spike B measured its own — worth doing before this ADR is
finalized, so the comparison is complete on both sides.

## Comparison, now that both spikes are in

Neither spike is "done" or production-ready — both are exactly what they
were meant to be: focused, honestly-reported spikes, not finished
backbones. The real question this ADR needs answered isn't which spike
went faster (Spike B, by a wide margin, and its own author says not to
over-read that), it's **which option leaves a smaller list of
must-build, non-optional pieces for a four-person team with several
months left.**

On that framing: Option A's core mechanism comes with delivery
guarantees (retries, ordering, offline sync, receipts, media) already
built into the protocol, confirmed working end-to-end in this spike.
Option B's own findings list the equivalent guarantees as explicitly
unstarted work, comparable in scope to what a mature protocol already
solves. Both options share the same E2EE constraint — Option A resolves
it as a deliberate, documented decision (encryption off for moderated
rooms, confirmed working); Option B never had the tension to begin with,
because encryption was never built, not because it found a way around
the tradeoff.

**Neither spike's findings by themselves decide this ADR** — that's a
whole-team call, informed by both spikes, not either spike author's
recommendation alone. Status stays **Proposed**.

## Consequences

Whichever option is chosen becomes the foundation every other student's
service depends on (AI services read/write through it, moderation acts on
it, live audio may share auth with it). Getting this decision right early
matters more than getting it fast — hence the deliberate two-week box
rather than picking on day one.
