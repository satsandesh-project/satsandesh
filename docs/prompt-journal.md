# Prompt Journal — Student 2 (Platform & backbone)

Team practice: log the key prompts behind AI-assisted work, plus notable
corrections, so a human reviewer can trace how code was produced. See
`README.md` → Process.

> **Note:** this file was created ad hoc during the Week-1 gateway fixup,
> before the team agreed a journal format. **It will likely need
> consolidating with M4's later prompt-journal template** — treat the
> structure here as provisional, not as the standard.

## Week 1 — gateway, docker-compose, Caddy, Postgres init, env config

**Date:** 2026-08-10

**Prompt (summarized):** Fix the gateway skeleton so `docker compose up
--build` boots the whole backend shell with routing and DB init working.
Specifically: rename `gateway/Requirements.txt` to lowercase (case-sensitive
COPY was failing on Linux), write the missing/incomplete
`infra/caddy/Caddyfile`, move hardcoded Postgres credentials out of
`docker-compose.yml` into `.env` / `.env.example`, fix `.gitignore` so
`.env.example` is tracked while `.env` stays ignored, add
`db/init/001_init.sql` seeding a `schema_check` table mounted into
Postgres's `/docker-entrypoint-initdb.d`, add a minimal `ai-services`
FastAPI stub with `GET /health` on port 8001, extend the gateway with
`GET /db-check` to prove Postgres init ran, route `/ai/*` through Caddy to
`ai-services` and everything else to `gateway`, and add pytest health-check
tests for both services. Scope explicitly limited to gateway/compose/Caddy/
Postgres/env — not `.github/`, `docs/adr/`, `backbone/`, or `clients/`.

**Key corrections/decisions made during the session (not blindly accepted
from the first draft):**

- The working directory handed over (`Documents/SatSandesh/satsandesh`) was
  an untracked duplicate of a git-tracked WIP copy elsewhere on disk
  (`Documents/PROOO/satsandesh`) — asked for clarification instead of
  guessing which one to treat as canonical.
- Discovered the pre-existing git repository was rooted at the entire home
  directory (`C:\Users\veere`) rather than the project folder — a real risk
  of accidentally staging personal files (SSH keys, shell history, etc.) on
  a broad `git add`. Flagged it and, per direction, initialized a fresh
  `.git` scoped to the project directory instead of reusing the home-rooted
  one.
- Caddy healthchecks/health-endpoint checks use `python -c
  "urllib.request.urlopen(...)"` rather than installing `curl`, to avoid
  adding a package to the slim Python image just for a healthcheck.
- Added an empty `conftest.py` at each service root (`gateway/`,
  `ai-services/`) so `pytest` can import `main` regardless of which
  directory it's invoked from — otherwise `from main import app` in
  `tests/test_health.py` fails depending on cwd.
- `/ai/*` must be matched with `handle_path` *before* the catch-all
  `handle` block in the Caddyfile, or Caddy would route every request to
  the gateway regardless of path.

**Verification:** see "Run & Verify" in the root `README.md`. Full
end-to-end verification completed:

- `docker compose config` — validates the compose file parses and
  `${VAR}` interpolation resolves correctly from `.env`.
- `python -m py_compile` on every new/edited `.py` file.
- `pytest` for both `gateway` and `ai-services` — both `/health` tests
  pass.
- `git check-ignore -v .env .env.example` — confirms `.env` stays
  ignored while `.env.example` is tracked.
- `docker compose up --build` — all four containers (postgres, gateway,
  ai-services, caddy) come up and report `healthy`.
- `curl http://localhost/health`, `/ai/health`, `/db-check` — all three
  return the expected JSON through Caddy.
- Volume persistence: inserted an extra `schema_check` row, ran
  `docker compose down` + `up`, and confirmed via `psql` that both the
  original seed row and the extra marker row were still present —
  proves the named `pgdata` volume persists data across a full
  container teardown/recreate, not just that init re-ran.

**Environment note (resolved):** Docker Desktop on this dev machine
initially hit an upstream bug (crash-looping on AF_UNIX socket creation
for its Inference/Secrets Engine components — see
[docker/desktop-feedback#460](https://github.com/docker/desktop-feedback/issues/460)),
unrelated to this project's config. It persisted across a stale-file
cleanup, a reboot, a factory reset, and an upgrade to Docker Desktop
4.86.0. Root cause turned out to be a corrupted Windows Winsock catalog;
running `netsh winsock reset` (admin) + a restart fixed it permanently.

## Week 1 — close-out pass

**Date:** 2026-08-17

**Prompt (summarized):** Re-ran the Week-1 scope as a checklist to catch
anything missed, with two requirements stated more precisely than in the
first pass: Caddy must `depend_on` gateway **and** ai-services as
*healthy* (not merely started), and pytest must cover `/db-check` as well
as `/health`. Also asked for a "Run & Verify" section in
`gateway/README.md` specifically (the first pass put one in the root
`README.md`), and for this journal to carry a note that it may need
consolidating with M4's later template.

**Changes made in this pass:**

- `docker-compose.yml`: Caddy's `depends_on` upgraded from the shorthand
  list form (which only waits for *started*) to `condition:
  service_healthy` on both gateway and ai-services. Without this, Caddy
  can start accepting traffic and return 502s while uvicorn is still
  booting.
- `gateway/tests/test_health.py`: added three `/db-check` tests — happy
  path, empty-table (500), and DB-unreachable (503). They fake psycopg's
  connection/cursor via `monkeypatch` rather than requiring a live
  Postgres, so `pytest` still runs without `docker compose up`. Faking
  was necessary because psycopg's connection *and* cursor are both
  context managers, so the stand-ins need `__enter__`/`__exit__`.
- `gateway/README.md`: added an endpoints table, a "Run & Verify"
  section, and a troubleshooting note that init scripts only run against
  an empty data dir (so a stale `pgdata` volume needs
  `docker compose down -v`).

**Deliberate deviation from the prompt:** the prompt asked for the AI
stub at `services/ai-services/`, but also asked to match the existing
`gateway/` layout for consistency. The repo already had a top-level
`ai-services/` directory (with a teammate-authored `README.md`), and
ADR 0001 specifies top-level folders matching the architecture. Nesting
it under a new `services/` directory would have contradicted both, so it
stayed at `ai-services/`. Flagging rather than silently choosing.

## Week 2 — Spike B: custom-lite backbone (FastAPI WebSockets + Postgres outbox)

**Date:** 2026-08-18

**Prompt (summarized):** Spike-then-decide, not build-then-decide: prove
or disprove a custom-lite chat backbone (FastAPI WebSockets + Postgres
outbox) as Option B in ADR 0002, in parallel with a teammate's Spike A
(Matrix/Tuwunel). Scope: `backbone/` only, plus a short Step 0
housekeeping list (move the stray ADR draft into `docs/adr/`, split
test-only deps out of each service's `requirements.txt`, fix a wrong
README claim about running tests inside the container). Explicit
constraint: must not break the already-verified Week 1 stack. Five
required, individually-tested behaviours: online delivery, offline
queueing with ordered backlog drain, ordering under a burst, crash
safety (kill mid-dispatch, confirm nothing lost), and two-dispatcher
concurrency (no double-delivery). Deliverable: real measurements, not
impressions — a finished-feeling spike that skipped honest measurement
would have missed the actual point of the exercise.

**Scope note flagged rather than silently resolved:** the prompt's scope
line said "do NOT touch ... ai-services/", but Step 0b explicitly named
`ai-services/requirements.txt` for the dev-deps split. Did the narrow,
explicit instruction (split deps in both services) rather than the
general exclusion rule, and said so in the commit message rather than
picking one silently.

**What was actually built** (`backbone/spike-custom-lite/`): the outbox
pattern in its simplest honest form. `POST /send` writes a message and
one outbox row per recipient in one transaction (durability starts the
moment that commits). A background dispatcher polls with `SELECT ...
WHERE status='pending' ORDER BY id FOR UPDATE SKIP LOCKED`, pushes to
whoever's connected via an in-memory registry, marks delivered or leaves
pending. All five behaviours pass; real numbers (duplicate counts,
resource usage, timing) are in `docs/adr/0002-chat-backbone.md`'s "Spike
B findings" section, not repeated here.

**Design decision, stated rather than hidden:** the dispatcher runs as a
background `asyncio` task inside the same process as the WebSocket
server, not as a genuinely separate OS process. The connection registry
is in-process memory, so a truly separate dispatcher process couldn't
reach a live socket without a pub/sub bridge (e.g. Redis) that doesn't
exist in this spike. This means "two dispatcher instances" (behaviour 5)
is tested as two concurrent Postgres transactions racing to claim rows —
which is what actually exercises `SKIP LOCKED` — rather than as two
literal `docker run` processes. Documented in `dispatcher.py`'s
docstring, not left implicit.

**What broke, and what each fix actually took** (the honest version, not
the tidy one):

1. **psycopg async + Windows' default event loop.** First real run
   against Postgres failed immediately: `psycopg.InterfaceError:
   Psycopg cannot use the 'ProactorEventLoop' to run in async mode`.
   Fixed for the pytest path by setting
   `asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`
   at the top of `db.py` (imported before any loop exists, in that path)
   — worked immediately for all four pytest-based behaviours.

2. **The same fix silently did not work for the actual server process.**
   Running `python -m uvicorn app:app` hit the identical error, despite
   the policy fix already being in place. Root cause (found by reading
   uvicorn's source, not by guessing): uvicorn 0.52 resolves its own
   event loop via `Config.get_loop_factory()` and passes it straight to
   `asyncio.run(..., loop_factory=...)` — a Python 3.12+ mechanism that
   bypasses the global event loop *policy* entirely. `asyncio_loop_factory`
   hardcodes `ProactorEventLoop` on `win32` unconditionally. No amount of
   setting the policy earlier fixes this, because uvicorn never looks at
   the policy. Real fix: a custom loop factory (`loop_factory.py`) passed
   directly to `uvicorn.run(..., loop="loop_factory:factory")` via a
   dedicated entrypoint (`run.py`) instead of the bare `uvicorn` CLI. This
   was the single most time-consuming problem this session — not because
   the eventual fix is complex (it's five lines), but because the first,
   plausible-looking fix (event loop policy) silently didn't apply to
   this specific entrypoint, and nothing failed loudly about *why* until
   traced through uvicorn's actual source.

3. **First crash-safety test design proved nothing.** 15 messages, killed
   after an 800ms window — all 15 had already been delivered before the
   kill landed. Passed, but didn't test what it claimed to. Fix: scaled
   to 300 messages pre-seeded via confirmed writes, recipient connects
   only afterward, tightened kill window. Second attempt overcorrected
   (killed before the dispatcher's first poll cycle even fired: 0
   delivered). Third attempt landed exactly on a batch boundary (50/300,
   0 duplicates) — informative (proves multi-cycle recovery works) but
   still didn't exercise the actual duplicate-risk window, because
   Postgres transactions are all-or-nothing: an interrupted batch either
   fully commits or fully rolls back, no partial state to catch by luck.
   Final fix: a test-only fault-injection knob
   (`SPIKE_DELIVERY_DELAY_MS`, zero effect unless explicitly set) that
   slows delivery enough for an external kill to reliably land *inside*
   an open transaction. That run finally produced real duplicates (9-10,
   varies run to run) with zero message loss — the actual claim in
   `dispatcher.py`'s docstring, now demonstrated rather than asserted.

4. **`docker compose down` (no profile) doesn't stop profile-scoped
   containers.** Left `spike-backbone` orphaned and running after a
   supposedly-clean teardown; the next `docker compose down` reported
   "Network ... Resource is still in use." Not a bug in this repo's
   config — a Compose profiles behavior worth knowing: tearing down
   profile-scoped services needs the same `--profile` flag as bringing
   them up.

**Claude Code reliability, honestly:** schema design, the outbox pattern
itself, and the `SKIP LOCKED` concurrency query were correct on the first
pass — no bugs surfaced in testing on any of that. `db.ensure_schema()`'s
statement-splitting had a near-bug caught by re-reading the code before
running it (a naive filter would have silently dropped the 2nd/3rd
migration statements) rather than by a failing test — worth noting
because it means at least one mistake didn't get caught by the "run it
and see" safety net this session leaned on for everything else. The
event-loop-policy-vs-loop_factory issue (#2 above) was a genuine,
non-hallucinated platform-compatibility gap: real APIs, used correctly,
that still didn't compose the way a first read of psycopg's error
message suggested. No hallucinated methods or invented APIs observed
anywhere this session.

**Time:** ~44 minutes wall-clock this session end to end, of which
roughly half (~20-25 min) was unrelated local Docker Desktop
environment trouble (an auto-update silently hung waiting on a UAC
prompt nothing was there to click) rather than spike work. Actual
spike development — schema through all 5 behaviours passing — was
roughly 20 minutes. Caveat worth stating plainly: "wall-clock minutes in
an AI-assisted session" is not the same unit as "a developer's focused
hours," and the ADR's "Time to first working mechanism" criterion says
so explicitly rather than implying the numbers are directly comparable
to however long Spike A's teammate reports.

**Lines of code:** 769 across the spike's `.py` files — 368 application
code (`app.py`, `db.py`, `dispatcher.py`, `registry.py`, `run.py`,
`loop_factory.py`), 401 test code (five behaviours plus one extra
fan-out check, plus `conftest.py`). Test code exceeding app code is
itself worth noting: proving crash-safety and concurrency claims
honestly took more code than the mechanism being proven.

**Verification:** `docker compose up --build` (no profile) reconfirmed
healthy after every change that touched shared files (Step 0's
requirements/dockerignore edits). `docker compose --profile spike up
--build` starts all 5 containers healthy, `spike-backbone` included.
`pytest` (6 tests: 4 delivery + concurrency + crash-safety) passes
against real Postgres without needing the `spike` profile running at
all — only the base `docker compose up` for Postgres itself. `docker
stats` measured the spike container's real idle footprint (~42MB RAM,
~3% CPU) rather than guessing.
