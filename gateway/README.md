# gateway/

**Owner:** Student 2 (Platform & backbone)

FastAPI gateway. Owns authentication, routing, and WebSocket fan-out.
This is the single entry point clients and AI services talk to — nothing
talks directly to the backbone or ai-services except through here.

Status: skeleton up. `GET /health` and `GET /db-check` (confirms Postgres
init ran) are live. Auth, real routing, and WebSocket fan-out still pending
the backbone decision (see `docs/adr/`).
