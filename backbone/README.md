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

Status: spike in progress.
