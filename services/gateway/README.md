# SatSandesh Gateway (`services/gateway/`)

Owned by M3. This is the single front door for every SatSandesh client — nothing
talks to the chat backbone, the AI services, or LiveKit directly. Written for a
teammate who has never opened this folder.

## Scope

This is the **Month 1, Week 1 gateway skeleton**: a FastAPI app object, a health
check, auth stubs, and a WebSocket echo endpoint. Nothing more.

**Out of scope this week:** routing to the chat backbone, proxying to
`services/ai/`, LiveKit integration, a database, real authentication, voice or
translation features. Those come later — see below.

**Relationship to `services/ai/`:** that folder (Month 2 on the compressed
schedule, also owned by M3 — see `docs/DECISIONS.md`) already has working
contracts and a mock server. This gateway will eventually proxy to it using the
Pydantic schemas in `contracts/ai/`, but no such routing exists yet.

## Quickstart

```bash
cd services/gateway
py -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .[dev]
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs` once it's running. Routes so
far: `/health` and `/health/ready` (no auth), `/me` and `/moderator-only`
(require a `Bearer` token — any non-empty token satisfies the Week 1 auth
stub), and `/ws` (WebSocket echo, token via a `?token=` query param). See
"What is stubbed and who replaces it" below before treating any of these as
real auth.

## Configuration

Settings are loaded from environment variables (or a local `.env` file) via
`app/config.py`. Copy `.env.example` to `.env` and fill in real values —
`.env` is gitignored, `.env.example` is the committed template. A missing
required variable (`DATABASE_URL`, `JWT_SECRET`) crashes the app at startup
with a readable error, not silently the first time a route needs it.

**Docker gotcha to know before it bites you:** inside Docker Compose, this
service reaches other containers (Postgres, etc.) **by service name**, not
`localhost`. `DATABASE_URL` in your local `.env` will look like
`postgresql://user:pass@localhost:5432/db`, but the value used inside the
container must point at `postgres:5432` (the Compose service name) instead —
`localhost` inside a container refers to the container itself, not the host
or its sibling containers. This is the classic first-week Compose bug; when
this service gets containerized, its Compose-specific env file (or the
`environment:` block in `docker-compose.yml`) needs its own `DATABASE_URL`
distinct from the one in this local `.env`.

## What is stubbed and who replaces it

`app/auth.py`'s `user_from_token(token)` is the **single swap point** for real
JWT verification. Both `get_current_user` (HTTP, reads the `Authorization`
header) and `app/ws.py`'s `/ws` route (WebSocket, reads a `?token=` query
param) call through this one function — editing its body is enough to wire up
real auth for both transports at once, no route or call site needs to change.

**Do not rebind the name — the two call sites resolve it through different
mechanisms, and a rebind at runtime makes them silently disagree instead of
both going stale together:**

- `get_current_user`, defined in `app/auth.py` itself, calls
  `user_from_token(...)` as a plain global lookup. Python resolves that name
  against `app/auth.py`'s own module namespace **every time the function
  runs** — rebind `app.auth.user_from_token` at runtime and `get_current_user`
  picks up the new implementation on its very next call.
- `app/ws.py` instead does `from app.auth import user_from_token` at the top
  of the file. That statement copies the function *object* into `app/ws.py`'s
  own namespace once, at import time. A later rebind of
  `app.auth.user_from_token` has no effect on that copy — `/ws` keeps calling
  whatever `user_from_token` was when `app/ws.py` was first imported, for the
  life of the process.

So rebinding the name somewhere instead of editing `user_from_token`'s body
in place would make HTTP auth (`/me`, `/moderator-only`) switch over
immediately while WebSocket auth (`/ws`) silently keeps accepting the old
stub — worse than either path breaking outright, since the HTTP tests would
pass and give false confidence that the swap worked everywhere. Replace the
body of `user_from_token` in place; don't introduce a second name and point
to it.

## Testing

```bash
cd services/gateway
./.venv/Scripts/python.exe -m pytest tests/
```

12 tests across 4 files, all passing:

- `tests/test_health.py` — `/health` returns `{"status": "ok"}`; `/health/ready`
  returns the expected `{"status", "checks"}` shape; `/health` is proven
  dependency-free (a monkeypatched socket connect must not fire).
- `tests/test_auth.py` — `/me` rejects a missing token (401) and returns the
  stub user given one; `/moderator-only` rejects the wrong role (403); a
  dependency override swaps the current user for tests.
- `tests/test_config.py` — missing `DATABASE_URL`/`JWT_SECRET` fails loudly at
  construction; settings load correctly from env.
- `tests/test_ws.py` — `/ws` echoes a message, rejects a missing token with
  close code 1008, and handles a client disconnect cleanly.

## Development

```bash
cd services/gateway
./.venv/Scripts/python.exe -m ruff check app tests     # lint
./.venv/Scripts/python.exe -m ruff format app tests    # format
```

Ruff handles both linting and formatting, same as `services/ai/` — no black.
See `docs/DECISIONS.md` for the one place this service's dependency pinning
does deliberately differ from `services/ai/`'s (exact pins + `requirements.txt`
as a lock file, vs. floating ranges there).
