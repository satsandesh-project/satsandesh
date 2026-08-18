# SatSandesh

A moderated, multilingual, elder-first messaging platform for a spiritual community.

"SatSandesh" (truthful message) is a working title.

## What this is

An invitation-only messaging app for a spiritual organization — WhatsApp-like in
familiarity, but different by design in four ways: values-aligned content
stewardship, a language bridge (voice/text translation across Indian languages),
first-class satsang and bhajan sessions, and an elder-first experience.

See the full project proposal for details on scope, architecture, and rationale.

## Team & ownership

| Member    | Role                          | Owns                                                                 |
|-----------|--------------------------------|-----------------------------------------------------------------------|
| Student 1 | Product & elder experience     | Reflex elder client, onboarding flows, accessibility, synced-lyrics UI |
| Student 2 | Platform & backbone            | Gateway, Matrix/custom backbone, PostgreSQL, Docker, deployment, push, backups |
| Student 3 | Speech & language AI           | ASR/MT/TTS services, pipeline latency, GPU serving, voice-search stretch |
| Student 4 | Stewardship, quality & pilot    | Moderation prompts/classifier, moderator console, tests/CI, red-teaming, pilot logistics |
| Supervisor | Prof. Korra Sathya Babu        | Weekly reviews, architecture PRs, org-liaison escalation, ethics approval |

This table is the standing/long-term split. For what ships each sprint and
who's blocked on whom, see `docs/work-breakdown.md`.

## Repo layout

```
satsandesh/
├── gateway/              # FastAPI gateway — auth, routing, WebSocket fan-out
├── backbone/             # Chat backbone — Matrix bot (Option A) or custom FastAPI+Postgres (Option B)
├── ai-services/          # ASR / MT / TTS / moderation services
├── clients/
│   ├── elder-app/        # Reflex elder PWA
│   └── admin-console/    # Reflex admin/moderator console
├── infra/
│   ├── caddy/            # Reverse proxy / HTTPS config
│   └── backups/          # Backup scripts (pg_dump + restic)
├── docs/
│   ├── adr/               # Architecture Decision Records
│   ├── SRS.md             # Software Requirements Spec
│   └── policy-taxonomy.md # Content stewardship taxonomy
└── .github/workflows/     # CI pipelines
```

## Process

- Scrum-lite, 16 fortnightly sprints, demo every second Friday.
- Definition of Done: code + tests + docs + deployed to staging + demoed.
- AI writes code, humans own it — nothing merges unread. Tests first. Small PRs.
- Every student keeps a prompt journal (prompts + corrections behind each feature).
- Security checklist run every sprint.

## Status

Month 1 — foundations. Backbone architecture spike (Matrix/Conduit vs custom-lite)
in progress; see `docs/adr/`.

## How to run locally

### Prerequisites

- Docker Desktop (Windows/Mac) or Docker Engine + Compose plugin (Linux)

### Run & Verify

```bash
# 1. Copy the env template and fill in real dev values (never commit .env)
cp .env.example .env

# 2. Build and start everything
docker compose up --build
```

Wait until `docker compose ps` shows `postgres`, `gateway`, `ai-services`, and
`caddy` all as `healthy`, then check each route (all go through Caddy on
port 80 — no other ports need to be open):

```bash
curl http://localhost/health      # -> {"status":"ok"}                gateway
curl http://localhost/ai/health   # -> {"status":"ok","service":...}  ai-services
curl http://localhost/db-check    # -> {"status":"ok","schema_check":"db init ran"}
```

`db-check` confirms `db/init/001_init.sql` ran on first Postgres boot and
seeded the `schema_check` table.

Data persists across restarts via the named `pgdata` volume:

```bash
docker compose down     # stop containers, keep the volume
docker compose up       # data from before is still there
```

To wipe the database and re-run init from scratch:

```bash
docker compose down -v  # removes the named volume too
```

### Running tests

Each service has its own `pytest` suite. Run these locally (in a venv or
just directly) — not inside the container: `.dockerignore` excludes
`tests/` from the image, so the tests aren't there to run.

```bash
cd gateway && pip install -r requirements.txt -r requirements-dev.txt && pytest
cd ai-services && pip install -r requirements.txt -r requirements-dev.txt && pytest
```
