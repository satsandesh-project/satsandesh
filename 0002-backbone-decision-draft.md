# ADR 0002: Chat Backbone — Matrix (Tuwunel) vs Custom-Lite

**Status:** Proposed (spike in progress)
**Date:** 2026-08-01
**Author(s):** Student 2

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

**Not yet made — spike in progress.** Recommended approach per the
proposal: attempt Option A (using Tuwunel, not the originally-named
Conduit) first, time-boxed to two weeks. If it's fighting the team hard by
around day 10, pivot to Option B without treating that as a failure.

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

## Decision criteria (to evaluate objectively, not just "did it feel ok")

| Criterion | How we'll check |
|---|---|
| Time to first working appservice bot | Hours spent, stop-clock from day 1 |
| Can it run without E2EE cleanly | Confirm plaintext readable by bot |
| Resource use on our deployment target | RAM/CPU at idle + light load |
| Team comprehension | Can each of us explain the flow, not just Student 2 |
| AI-assistant helpfulness | Are Claude Code suggestions reliable for this stack, or frequently wrong/hallucinated |

## Consequences

Whichever option is chosen becomes the foundation every other student's
service depends on (AI services read/write through it, moderation acts on
it, live audio may share auth with it). Getting this decision right early
matters more than getting it fast — hence the deliberate two-week box
rather than picking on day one.
