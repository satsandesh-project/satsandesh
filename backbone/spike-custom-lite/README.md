# backbone/spike-custom-lite/

**Owner:** Student 2 (Platform & backbone)

Spike B for ADR 0002: a minimal custom-lite chat backbone (FastAPI
WebSockets + a PostgreSQL outbox), built to answer one question honestly
— can this pattern deliver messages reliably and in order, survive a
crash, and handle two dispatchers without double-delivery?

**This is spike code, not production.** No auth (`user_id` is a trusted
query param), no connection pooling, no migration tooling, no
reconnection backoff. See `docs/adr/0002-chat-backbone.md` for the actual
findings, decision-criteria numbers, and recommendation.

## How it works

- `POST /send` writes a `spike_messages` row and one `spike_outbox` row
  per recipient, in a single transaction — the outbox pattern's whole
  point: the delivery *obligation* is durable on disk the moment this
  commits, independent of whether the process handling it survives.
- `GET /ws?user_id=...` accepts a WebSocket connection and registers it
  in an in-memory registry (`registry.py`).
- A background dispatcher task (`dispatcher.py`), started from `app.py`'s
  lifespan, polls `spike_outbox` for pending rows using
  `SELECT ... FOR UPDATE SKIP LOCKED`, pushes to connected recipients, and
  marks delivered — or leaves pending (incrementing `attempts`) if the
  recipient is offline.

## Running it

Not part of the default stack — only starts with the `spike` profile:

```bash
docker compose --profile spike up --build
curl http://localhost:8100/health
```

The base (no-profile) `docker compose up` stack is untouched by this;
`spike-backbone` isn't in it.

### Tests

All five behaviour tests run via plain `pytest` against a real Postgres —
they need the base stack's `postgres` service up (`docker compose up`,
no profile needed), but not the `spike` profile itself:

```bash
pip install -r requirements.txt -r requirements-dev.txt
DATABASE_URL=postgresql://satsandesh:<password>@localhost:5432/satsandesh pytest -v -s
```

(`-s` shows the printed measurement lines — claimed/delivered counts,
lost/duplicate counts — which are part of what this spike is measuring,
not just pass/fail.)

Four of the five behaviours run fully in-process via Starlette's
`TestClient` (see `tests/test_delivery.py`, `tests/test_concurrency.py`).
The crash-safety test (`tests/test_crash_safety.py`) genuinely needs a
separate OS process — see its docstring for why, and `run.py` +
`loop_factory.py` for a Windows-specific event-loop fix it depends on.

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI app: `/health`, `/send`, `/ws`, lifespan-managed dispatcher task |
| `dispatcher.py` | Claim + deliver logic; the `FOR UPDATE SKIP LOCKED` query is the load-bearing line |
| `registry.py` | In-memory `user_id -> live sockets` map |
| `db.py` | Connection helper + idempotent schema bootstrap |
| `run.py`, `loop_factory.py` | Entrypoint working around a Windows-specific psycopg/uvicorn event-loop conflict — see their docstrings |
| `migrations/001_spike_schema.sql` | `spike_messages` / `spike_outbox`, separate from `db/init/` on purpose |
| `tests/` | The five required behaviours, plus one extra (multi-recipient fan-out) |
