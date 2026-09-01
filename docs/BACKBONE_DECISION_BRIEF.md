# Backbone Decision Brief: Option A (Matrix) vs Option B (custom-lite)

**Status:** Historical record. Written 2026-08-31 to support a decision that was
then made before this brief was circulated — PR #18 (2026-09-01) removed
`services/gateway/` and made `gateway/` + `backbone/` (Option A, Matrix/Tuwunel)
the one going forward. Kept as-is, unedited below, because it is the only
side-by-side record of what each implementation actually contained and what was
verified working in each — which matters for recovering M3's orphaned features.
See `docs/OWNERSHIP.md` for how the collision happened and how to prevent a repeat.

**Prepared:** 2026-08-31, by request, after two independent, incompatible backbone
implementations were discovered: Guna/Sandesh's (M3) custom-lite Postgres backbone,
already merged into `main`, and Veerendra's (M2) implementation of both options —
custom-lite and Matrix/Tuwunel — built in a separate personal repository and only
just brought into the shared one.

Every claim below was independently re-verified today (2026-08-31): dependencies
installed fresh, tests actually run against real Postgres / a real Tuwunel instance,
not taken from commit messages or PR descriptions on trust.

---

## 1. What the proposal originally said (Section 8)

> **Option A**: a Matrix homeserver (Conduit, Apache-2.0) with an Application-Service
> bot that intercepts events, runs the pipeline and injects renderings. It buys
> delivery semantics, offline sync, receipts and a media repository for free; its
> cost is the Matrix learning curve.
>
> **Option B**: a custom-lite backbone — FastAPI, WebSockets and a PostgreSQL
> outbox — with the simplest possible mental model, at the cost of re-implementing
> delivery and retry semantics.
>
> A two-week spike in Month 1 decides between them, recorded as an Architecture
> Decision Record... Recommendation going in: attempt A; fall back to B without guilt.

The team's own Month-1 schedule split this spike across two people in Week 2 (M1:
Spike A / Matrix, M2: Spike B / custom-lite), with an ADR due in Week 3. That ADR
(`docs/adr/0002-chat-backbone.md`) currently records both spikes' findings but its
**Status is still "Proposed," not decided** — and, separately, is not the same
document as the one on Veerendra's branch, which records "Option A formally
confirmed by the whole team" as of today. Those two ADRs disagree with each other.
Nobody has reconciled them yet.

---

## 2. What actually exists today, verified

### Option B — custom-lite (Sandesh, M3) — already in `main`

| | |
|---|---|
| Location | `services/gateway/` |
| Message scope | **1:1 direct messages *and* circles/announcements**, one unified model |
| Persistence | PostgreSQL: `users`, `circles`, `memberships`, `conversations`, `messages` |
| Delivery | Real-time WebSocket push, sent/delivered states, offline-queue sync (`sync.request`/`sync.batch`), Web Push (VAPID) scaffolding |
| Extra features | 30-second undo, audio-label endpoint, `quiet_hours` column |
| Tests | **95/95 passing** (re-run today against a fresh Postgres instance) |
| Live-verified | Yes — this is the backend the elder client (Week 4 M1, PR #17) actually talks to for the sent/delivered-status demo |
| Client integration | Already wired: the Reflex elder app's message-sending flow was built and demoed against this exact backend |

### Option A — Matrix/Tuwunel (Veerendra, M2)

| | |
|---|---|
| Location | `backbone/spike-matrix-a/` (+ `gateway/`, a separate implementation from Sandesh's) |
| Message scope | **Circles/announcements only** — the `CircleBackbone` interface has no 1:1 direct-message concept anywhere in it |
| Persistence | Tuwunel (Matrix homeserver) rooms for circles, room membership for members, room messages for announcements |
| Delivery | Pull-based via `list_messages` (Matrix has no push mechanism in this interface, per the code's own comment) |
| Tests | **38/38 passing** — 23 in `gateway/tests/`, 11 in `backbone/spike-custom-lite/tests/` (he built *both* options), 4 in `backbone/spike-matrix-a/circle_service/tests/`, the last of which required a real running Tuwunel instance and genuinely talked to it |
| Live-verified | Real staging deployment attempted and logged, including a genuine caught-and-fixed bug (`--env prod`'s backend port) and, as of today, two real security fixes (a privilege-escalation bug and a WebSocket auth-ordering bug) |
| Client integration | A placeholder Reflex client exists, wired to *his* gateway over WebSocket — separate from, and not the same client build as, the one actually demoed in Week 4 |

### A genuinely good architectural detail on M2's side

His `backbone/interfaces.py` defines `CircleBackbone` as an abstract base class that
implementations must subclass (not just structurally match) — meaning an incomplete
implementation fails to even instantiate, at startup, in the real container. This
is exactly the contract-first design the proposal asked for ("the client and the AI
services speak only to the gateway, the choice is contained either way"), and it's
why he was able to build *both* options behind the same interface and swap between
them. Worth preserving as a pattern regardless of which backbone wins.

---

## 3. The core asymmetry

This isn't really "Matrix vs. Postgres" in the abstract — it's two different-shaped
pieces of work:

- **Sandesh's side** solves the *whole* messaging problem (1:1 + circles), is
  already merged, already tested at 95 cases, and is the actual backend the client
  work you've seen demoed depends on.
- **Veerendra's side** solves *half* the problem (circles/announcements) via
  Matrix, plus separately built the Docker Compose skeleton, staging deployment
  infrastructure, and two real security fixes that don't depend on which backbone
  wins at all.

If Option A wins, someone still has to build 1:1 direct messaging on Matrix from
scratch — it doesn't exist yet on either side in that form. If Option B stays,
Veerendra's circles/Matrix work becomes throwaway (or a documented "we tried both,
here's why we picked B" artifact for the report), but his Compose/staging/security
work does not — none of that is backbone-specific.

---

## 4. What would need to happen either way

- **If Option B (already in `main`) stays**: reconcile Veerendra's `docker-compose.yml`,
  `infra/` staging setup, and his two security fixes into the shared repo structure
  (`services/gateway/`, not his `gateway/`). His Matrix/custom-lite backbone work
  gets archived, not deleted — it's real, tested, and worth keeping as documented
  spike evidence either way.
- **If Option A (Matrix) is chosen instead**: 1:1 messaging needs to be designed
  and built on Matrix (doesn't exist yet), and everything currently built against
  Sandesh's `services/gateway/` — including the Week 4 elder client demo — needs to
  be re-pointed at a new backend. That's not a small swap; the client's `send`/
  sent-delivered flow was built and tested against Option B specifically.
- **Either way**: the two conflicting ADRs need to be reconciled into one, signed
  off by both M2 and M3 (and ideally the whole team, per the original proposal's
  intent — "the supervisor personally" reviews architecture-touching PRs).

---

## 5. What this brief is *not*

This is a factual inventory, not a recommendation. Both implementations are real,
tested, and represent genuine effort. The decision belongs to the team — specifically
to Sandesh and Veerendra, who each built a working option and have the deepest
context on their own tradeoffs the numbers above can't fully capture (operational
complexity of running a Matrix homeserver long-term, team familiarity, the actual
Month-2 language-bridge pipeline's integration needs with whichever backbone is
chosen, etc.).
