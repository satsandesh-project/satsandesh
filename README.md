# SatSandesh

**A moderated, multilingual, elder-first messaging platform for devotional communities.**

Open source, self-hosted, built as seva. No advertising, no engagement traps, no data sold.

> **Status: early development.** Month 1 of a 3-month build. Nothing here is usable yet.

---

## What this is

India's devotional communities live on general-purpose WhatsApp groups, where satsang content is
diluted by forwards, commerce, arguments and misinformation. Elders in particular find the noise
stressful and the interface unforgiving.

SatSandesh is a private, invitation-only communication app that differs by design in four ways:

1. **Content stewardship** — every message passes a values-aligned screening step, so the space stays
   devotional and free of disputes, commercial chatter and explicit material.
2. **A language bridge** — voice notes spoken in one Indian language are delivered to each receiver as
   text *and* natural speech in that receiver's own chosen language.
3. **Satsang and bhajan sessions** — one-to-many broadcast, plus bhajan rooms with a single-lead
   "floor" so participants never talk over one another.
4. **Elder-first experience** — voice-driven, large targets, forgiving, honest about ₹7,000 Android
   phones and rural bandwidth.

The guiding engineering principle is **assemble, don't rebuild**: mature open source supplies message
delivery, live audio, speech recognition, translation and synthesis. Team effort goes into the
differentiating parts — the elder experience, the language bridge, stewardship and the satsang
experience.

---

## How stewardship works

The model is a **parcel stamp, not an archive**. A message is read in transit, judged, and passed on.
Content that passes is never registered anywhere beyond normal message storage.

- **Explicit / vulgar / abusive** → blocked, with a clear notice to the sender stating why and how to
  request a review. Never a silent deletion.
- **Argumentative or disputable** → the *sender* is warned before it sends ("this reads like a
  complaint about someone — send anyway, or rephrase?"). The sender decides. No deletion.
- **Personal / off-topic** → a private, kindly-worded nudge, in the sender's own language.
- **Everything else** → passes.

Two properties this design must preserve:

1. **Appeals need something to appeal to.** Zero retention makes review impossible and the false-positive
   rate unmeasurable. The intended resolution is a short **quarantine for blocked messages only**
   (e.g. 72 hours, visible only to whoever handles appeals, auto-purged). Passing messages are never
   quarantined.
2. **Over-blocking is a first-class defect.** An elder's message about a sick spouse vanishing without
   explanation is worse than a dispute getting through. False-hold rate is a tracked metric, not an
   afterthought.

---

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

> **Note:** `services/gateway/` (M3's) is the intended real gateway going forward —
> restored after briefly being removed from this branch (see git history / the
> prompt journal for both sides of that back-and-forth). **Not yet actually
> functional as the running system**, though: it has a genuinely well-designed
> Postgres schema (real Alembic migrations, DM support, idempotency) but its own
> README describes it as a Week 1 skeleton — auth is an explicit stub (any
> non-empty token accepted), and its WebSocket route is a plain echo with no
> persistence, broadcast, or backbone integration at all. It also has no
> Dockerfile/compose entry — never containerized. `gateway/` (Member 2's) is what's
> actually deployed and running on the real staging server right now, with real
> auth, WS relay, and Matrix/Tuwunel integration (ADR 0002). Making
> `services/gateway/` the real running system needs that gap closed first — see
> `docs/prompt-journal.md`'s Week 4 entries for the specifics and the open
> question of whether that keeps Matrix (ADR 0002) or not.

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
│   ├── deploy/           # Deploy scripts (college server + personal/team repo sync)
│   └── backups/          # Backup scripts (pg_dump + restic)
├── services/              # Team's canonical service layout (gateway, ai) -- see note above
├── contracts/             # Shared request/response contracts between services
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
resolved (Option A, Matrix/Tuwunel — see `docs/adr/0002-chat-backbone.md`). Member 2's
platform work (gateway, deployment, Week 1-4) is complete and deployed to a real staging
host; see `docs/prompt-journal.md` for the full history.

## How to run locally (Member 2's `gateway`/`docker-compose.yml` tree)

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

The Matrix backbone profile (ADR 0002's chosen option) starts alongside the base stack with:

```bash
docker compose --profile matrix up -d
```

### Running tests

Each service has its own `pytest` suite. Run these locally (in a venv or
just directly) — not inside the container: `.dockerignore` excludes
`tests/` from the image, so the tests aren't there to run.

```bash
cd gateway && pip install -r requirements.txt -r requirements-dev.txt && pytest
cd ai-services && pip install -r requirements.txt -r requirements-dev.txt && pytest
```
