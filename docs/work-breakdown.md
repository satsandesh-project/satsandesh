# Team Work Breakdown — Month 1

_Week 1 below; Week 3 is at the end of this file._

_Status: draft — Student 2's Week 1 items below are complete and merged.
Students 1/3/4: please confirm or adjust your own section before we treat
this as final. See `README.md` → Team & ownership for the standing
role table this document expands on._

## Purpose

The README's ownership table says *what each person owns long-term*. This
doc is the working breakdown of *what ships when*, so we can tell at a
glance who's blocked on whom and what "done" means for Week 1 specifically.
Update it every sprint rather than keeping history here — git history is
the record of what changed and when.

## Cross-cutting dependencies

- Student 3's AI services and Student 4's moderation console both call
  through the gateway, not the backbone directly (see `gateway/README.md`)
  — so both are soft-blocked on Student 2's gateway routing being live,
  which it now is (`docker compose up --build`, see root `README.md`).
- Student 4's CI (`.github/workflows/ci.yml`) is currently a placeholder
  that no-ops — it starts doing real work once there's `lint`/`test`
  config to point at from each component's own requirements.txt.
- The backbone decision (Matrix/Tuwunel vs. custom-lite, `docs/adr/0002-*`)
  is still an open spike. Student 1's client and Student 3's AI services
  can build against the gateway's contract in the meantime without
  waiting on that decision — the gateway is the boundary that insulates
  everyone else from it.

## Student 1 — Product & elder experience

**Owns long-term:** Reflex elder client, onboarding flows, accessibility,
synced-lyrics UI.

**Week 1 goal (proposed — confirm/adjust):**
- Reflex project scaffolded under `clients/elder-app/`, matching the
  layout convention `gateway/` and `ai-services/` now use (own
  `README.md` status, own `requirements.txt`/`pyproject.toml`).
- One elder-facing screen rendering (even a static placeholder) to prove
  the Reflex toolchain works end-to-end on your machine.
- Start the 6–8 elder contextual interviews referenced in
  `docs/SRS.md` §3 — needed before real UI decisions can be made.

## Student 2 — Platform & backbone

**Owns long-term:** Gateway, Matrix/custom backbone, PostgreSQL, Docker,
deployment, push, backups.

**Week 1 — done:**
- `docker compose up --build` boots the full backend shell: `postgres`,
  `gateway`, `ai-services`, `caddy`, all healthy.
- Gateway `GET /health` and `GET /db-check`; ai-services `GET /health`,
  routed through Caddy (`/` and `/ai/*`).
- Postgres init script seeds and proves itself via `/db-check`; named
  volume persistence verified (see `docs/prompt-journal.md`).
- Secrets out of `docker-compose.yml` into `.env`/`.env.example`.
- pytest health-check coverage for gateway and ai-services.

**Next up:** backbone spike (ADR 0002 — Tuwunel vs. custom-lite),
time-boxed per that ADR's spike plan.

## Student 3 — Speech & language AI

**Owns long-term:** ASR/MT/TTS services, pipeline latency, GPU serving,
voice-search stretch goal.

**Week 1 goal (proposed — confirm/adjust):**
- `ai-services/` currently has only the health-check stub Student 2 added
  as scaffolding (`GET /health` on port 8001) — first real work is
  claiming that layout: pick the first model to stand up (ASR is the
  natural starting point, since MT/TTS depend on transcribed text) and
  add it as its own route/module, not a rewrite of the stub.
  See `ai-services/README.md` for the current status.
- Spike GPU serving options against the `< 10s p90` latency budget in
  `docs/SRS.md` §4 for a 30-second voice note, and record findings —
  this budget is the one non-functional requirement most likely to
  drive backend/deployment decisions later, so surfacing early is
  valuable even before a model is production-ready.

## Student 4 — Stewardship, quality & pilot

**Owns long-term:** Moderation prompts/classifier, moderator console,
tests/CI, red-teaming, pilot logistics.

**Week 1 goal (proposed — confirm/adjust):**
- Turn `.github/workflows/ci.yml` from placeholder into something real:
  point `pip install -r` at each component's actual requirements file
  (`gateway/requirements.txt` and `ai-services/requirements.txt` both
  exist now) and run each service's `pytest` suite in CI, not just
  locally.
- Start drafting the zero-shot moderation prompt against the taxonomy in
  `docs/policy-taxonomy.md` — that doc is still missing organization-
  authored exemplars, which is a blocker worth raising with the
  supervisor/org contact early rather than late.

## Supervisor — Prof. Korra Sathya Babu

**Owns:** Weekly reviews, architecture PRs, org-liaison escalation,
ethics approval.

**Relevant this week:** the backbone ADR (0002) recommends attempting
Matrix/Tuwunel first, time-boxed to two weeks — worth a quick sanity
check before Student 2 sinks real time into the spike. Also: ADR 0002
flags that full E2EE isn't compatible with server-side moderation/
translation as designed — worth confirming this is written into the
SRS's privacy/ethics section, not just left in the ADR.

---

# Week 3

## Backbone decision status — READ BEFORE BUILDING ON THIS (2026-08-19)

ADR 0002 (Matrix vs custom-lite) is **still open, and now past its
two-week time-box.** Spike B (custom-lite) reported; Spike A
(Matrix/Tuwunel) has not, so no comparison has happened.

Week 3's circles/announcements work is **proceeding behind an abstract
interface** (`backbone/interfaces.py`) rather than waiting on that
decision or assuming its outcome. The concrete implementation wired up
for now is **Option B (custom-lite) — chosen because it is the only
working option that exists, not because it won anything.** This is
provisional and **must be revisited once Spike A's findings land.**

Please don't read "Option B is what's currently running" as "the team
picked Option B." Nobody has picked anything yet.

## Student 2 — Platform & backbone

**Week 3 — circles (groups) + memberships, announcement channels.**
Deliverable: "post to a circle works" — create a circle, manage its
members, post one announcement, and have every member receive it
(including members who were offline at post time).

Built against `backbone/interfaces.py` so the eventual ADR 0002 outcome
is a contained implementation swap rather than a gateway rewrite.

**Blocked-on / gap found this week:** Week 2's "data model + migrations"
task (users / circles / memberships schema, Student 3) **has not landed
in this repo** — no such schema exists on `main` or anywhere in the tree,
confirmed by search. Rather than silently invent a competing version,
Week 3 defines only the minimum circles/membership tables it needs, under
the existing `spike_` prefix convention, scoped to the spike. **If a
canonical users/circles schema arrives later, these will need
reconciling** — flagged here rather than discovered as a conflict during
a merge. Whoever owns the canonical data model should treat this as a
known duplicate to resolve, not as a decision already made.

## Students 1 / 3 / 4 — Week 3

_To be filled in by each owner. Student 2's section above reflects only
its own scope (circles/announcements); another student owns 1:1
messaging this week._
