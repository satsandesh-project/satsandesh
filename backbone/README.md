# backbone/

**Owner:** Student 2 (Platform & backbone)

Chat backbone — message storage, delivery, and sync. Two options under
evaluation via a Month 1 architecture spike:

- **Option A:** Matrix homeserver (Conduit, Apache-2.0) with an
  Application-Service bot that intercepts events, runs the pipeline, and
  injects renderings.
- **Option B:** Custom-lite backbone — FastAPI + WebSockets + a
  PostgreSQL outbox.

Decision to be recorded in `docs/adr/`.

**Status: decision still OPEN, and past its two-week time-box.** Spike B
(custom-lite) is built and its findings are in
`docs/adr/0002-chat-backbone.md`; Spike A (Matrix/Tuwunel) has not
reported, so no comparison has happened yet.

## `interfaces.py` — the contract

`interfaces.py` defines `CircleBackbone`: what any backbone must provide
for circles and announcements. It is stdlib-only and depends on no
concrete implementation.

The gateway depends on this file and nothing else in here. That's what
makes resolving ADR 0002 an implementation swap rather than a rewrite —
and it's why Week 3's circles work could proceed without the decision
being made first.

| | |
|---|---|
| `interfaces.py` | The contract. |
| `spike-custom-lite/` | The only implementation today: Postgres outbox. `OutboxCircleStore` in `circles.py`. |
| a Matrix implementation | Would live here too, if ADR 0002 lands that way — rooms for circles, room membership for members, a room message for an announcement. |

Whichever way ADR 0002 goes, **that choice is not recorded here** —
`spike-custom-lite` being the only thing wired up reflects availability,
not a decision.
