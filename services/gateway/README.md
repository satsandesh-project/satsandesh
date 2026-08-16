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

Interactive docs at `http://localhost:8000/docs` once it's running. There are no
routes to try yet beyond what FastAPI generates automatically — this confirms
the app boots, nothing more.

## Testing

```bash
cd services/gateway
./.venv/Scripts/python.exe -m pytest tests/
```

No tests exist yet — this week's deliverable has no behavior beyond app startup
to test. `pytest-asyncio` and `httpx` are already pinned for when routes (and
their tests) land.

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
