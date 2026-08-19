# gateway/

**Owner:** Student 2 (Platform & backbone)

FastAPI gateway. Owns authentication, routing, and WebSocket fan-out.
This is the single entry point clients and AI services talk to — nothing
talks directly to the backbone or ai-services except through here.

Status: skeleton up. `GET /health` and `GET /db-check` (confirms Postgres
init ran) are live. Auth, real routing, and WebSocket fan-out still pending
the backbone decision (see `docs/adr/`).

## Endpoints

| Method | Path        | Purpose                                              |
|--------|-------------|------------------------------------------------------|
| GET    | `/health`   | Liveness. Returns `{"status":"ok"}`. Used by the compose healthcheck. |
| GET    | `/db-check` | Connects to Postgres via `DATABASE_URL` and confirms the seeded `schema_check` row exists. 503 if the DB is unreachable, 500 if the table is empty. |
| POST   | `/circles`  | Create a circle. `{name}` -> `{circle_id}`. |
| POST   | `/circles/{id}/members` | Add a member. `{user_id}`. Idempotent. |
| DELETE | `/circles/{id}/members/{user_id}` | Remove a member. Idempotent. Affects future announcements only. |
| GET    | `/circles/{id}/members` | Current members. |
| POST   | `/circles/{id}/announce` | Post to every current member. `{sender_id, body}` -> `{message_id}`. |
| GET    | `/circles/{id}/messages` | History, newest first. `?limit=&before=`. |

All circle routes return **503** when the backbone is unreachable — the
gateway being up doesn't mean its backbone is.

## The backbone boundary

Circle routes talk to a backbone through `backbone/interfaces.py`'s
`CircleBackbone` and nothing else. `gateway/circles.py` imports no
concrete backbone; `gateway/backbone_client.py` is the only file that
knows how to reach one, and `main.py` is the only place that names it.

That's deliberate: **ADR 0002 (Matrix vs custom-lite) is still open.**
`BACKBONE_URL` currently points at the custom-lite spike because it's the
only implementation that exists — not because the decision was made.
Swapping backbones should mean a new client class and a different URL,
not edits to route code.

## Run & Verify

The gateway is not meant to be run alone — it needs Postgres, and it is
reached through Caddy. Run the whole shell from the repo root:

```bash
cp .env.example .env
docker compose up --build
```

Confirm all four services report `healthy`:

```bash
docker compose ps
```

Then check the gateway's own routes through Caddy (port 80, no other ports
need to be open):

```bash
curl http://localhost/health
curl http://localhost/db-check
```

Expected:

```
{"status":"ok"}
{"status":"ok","schema_check":"db init ran"}
```

A successful `/db-check` proves three things at once: the gateway can reach
Postgres, `DATABASE_URL` is wired correctly, and `db/init/001_init.sql` ran
on first boot.

### Tests

The suite fakes the DB connection, so it needs no running containers.
Test deps (`pytest`, `httpx`) live in `requirements-dev.txt`, separate from
`requirements.txt` so they don't ship inside the Docker image:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

### Troubleshooting

- **`/db-check` returns 503** — Postgres isn't reachable. Check
  `docker compose ps` and that `DATABASE_URL` in `.env` uses the compose
  service name (`@postgres:5432`), not `localhost`.
- **`/db-check` returns 500** — the DB is up but `schema_check` is empty.
  Init scripts only run against an *empty* data directory, so if the volume
  predates `db/init/`, reset it with `docker compose down -v` and start again.
