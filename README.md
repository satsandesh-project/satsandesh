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
| Student 2 | Platform & backbone            | Matrix/custom backbone, PostgreSQL, Docker, deployment, push, backups |
| Student 3 | Speech & language AI           | ASR/MT/TTS services, pipeline latency, GPU serving, voice-search stretch |
| Student 4 | Stewardship, quality & pilot    | Moderation prompts/classifier, moderator console, tests/CI, red-teaming, pilot logistics |

This table is the standing/long-term split. For what ships each sprint and
who's blocked on whom, see `docs/work-breakdown.md`.

## Repo layout

> **Note (2026-09-05):** `services/gateway/` (M3's) is the one gateway going
> forward — the team confirmed this after an earlier branch had gone the other
> way (see git history / `docs/prompt-journal.md` for that back-and-forth).
> `gateway/` (Member 2's own FastAPI gateway, with real auth, a WebSocket
> relay, and a working Matrix/Tuwunel backbone integration, verified
> end-to-end against a real deployed server) has been removed as redundant.
> Its Matrix backbone — `backbone/spike-matrix-a/circle_service/` + Tuwunel,
> ADR 0002's originally-decided option — has been **superseded**: the team
> decided to remain on `services/gateway/`'s own Postgres-only circles
> implementation rather than complete that cutover, and the Tuwunel /
> `matrix-circle-service` containers have been removed from
> `docker-compose.yml` (there is no more `matrix` profile). Full
> reasoning and history: `docs/prompt-journal.md`'s Week 4 entries and
> `docs/adr/0002-chat-backbone.md`'s "Update (2026-09-05)" section.

```
satsandesh/
├── services/
│   └── gateway/          # FastAPI gateway (M3's) — auth, circles, messages, WebSocket
├── backbone/             # Chat backbone spikes — Matrix (Option A) and custom FastAPI+Postgres (Option B), both archived; services/gateway/'s own Postgres implementation is what actually ships
├── ai-services/          # ASR / MT / TTS / moderation services
├── clients/
│   ├── elder-app/        # Reflex elder PWA
│   └── admin-console/    # Reflex admin/moderator console
├── infra/
│   ├── caddy/            # Reverse proxy / HTTPS config
│   ├── deploy/           # Deploy scripts (college server + personal/team repo sync)
│   └── backups/          # Backup scripts (pg_dump + restic)
├── contracts/             # Shared request/response contracts -- contracts/chat/ is
│                          # services/gateway/'s wire format; contracts/ai/ is services/ai/'s
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
was originally resolved as Option A (Matrix/Tuwunel), then superseded — the team
decided to remain on `services/gateway/`'s own Postgres implementation instead
(see `docs/adr/0002-chat-backbone.md`'s "Update (2026-09-05)" section). Member 2's
platform deliverables (Docker Compose skeleton, backbone spikes, deployment, Week 1-4)
are complete — see `docs/prompt-journal.md` for the full history — and the currently
deployed gateway is `services/gateway/` (M3's); see the Repo layout note above.

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

Wait until `docker compose ps` shows `postgres`, `gateway`, `ai-services`,
`elder-app`, and `caddy` all as `healthy`, then check each route (all go
through Caddy on port 80 — no other ports need to be open):

```bash
curl http://localhost/health         # -> {"status":"ok"}               gateway
curl http://localhost/health/ready   # -> {"status":"ok","checks":...}  gateway, confirms Postgres reachable
curl http://localhost/ai/health      # -> {"status":"ok","service":...} ai-services
```

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
cd services/gateway && pip install -r requirements.txt && pytest
cd ai-services && pip install -r requirements.txt -r requirements-dev.txt && pytest
```
