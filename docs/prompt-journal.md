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

## Week 3 — circles (groups), memberships, and announcement channels

**Date:** 2026-08-19

**Prompt (summarized):** Build circles + memberships and announcement
channels; deliverable "post to a circle works". Scope limited to
`backbone/` and `gateway/`. Two instructions shaped the whole week more
than the feature itself did: (1) record honestly in ADR 0002 and the
work breakdown that the backbone decision is *still open and past its
time-box*, and that Week 3 proceeds behind an interface rather than
treating custom-lite as decided; (2) check for Week 2's
users/circles/memberships schema (Student 3's task) and **reuse it if
present, or flag the gap explicitly rather than silently inventing a
competing version**.

**The gap, checked before building anything:** there is no canonical
users/circles/memberships schema in this repo. Searched every `.sql`
file, grepped the tree, and checked `origin/main` for commits not held
locally — the only migrations are Week 1's `db/init/001_init.sql` and
Week 2's spike schema. So Week 3 defines only the minimum tables circles
need (`spike_circles`, `spike_circle_members`), under the existing
`spike_` prefix, and **deliberately does not create a `spike_users`
table** — nothing here needs one (user ids are caller-asserted strings,
there's no auth), and inventing one would be inventing an identity model
that belongs to whoever owns the canonical data model. Recorded as a
known duplicate-to-reconcile in `docs/work-breakdown.md`, so it surfaces
as a flagged decision rather than as a merge conflict later.

**Why an interface rather than waiting, or just building on custom-lite:**
Circles are needed by other students' work now; blocking them on an
unresolved architecture decision would be the worse trade. But building
directly against custom-lite would have quietly converted "the only
option that currently exists" into "the option we chose", which is
exactly the failure mode the ADR note now guards against. The interface
(`backbone/interfaces.py`) is what lets both be true at once: real
progress this week, and a contained swap when ADR 0002 actually
resolves. The ADR and work-breakdown notes say in plain words that the
current wiring is provisional and chosen for availability, not merit.

**How little the feature itself needed:** an announcement is exactly the
"one message, many recipients" fan-out Week 2's outbox already did and
already tested. `post_announcement` resolves membership into a recipient
list and hands it to the same transactional write. No delivery logic was
rebuilt — the dispatcher, `SKIP LOCKED` claiming, offline queueing,
ordering and crash recovery are untouched. Two semantics were worth
pinning down in the contract before there are two implementations to
disagree about: add/remove member are idempotent, and recipients are
resolved *at post time*, so removal affects future announcements only
while an already-written obligation still gets delivered.

**What broke, and what each cost:**

1. **The Week 2 migration splitter was quietly wrong.** `ensure_schema()`
   split each `.sql` file on `;` and executed the fragments. That splits
   inside SQL *comments* too. `002_circles.sql` had a comment containing
   "reconciling; that's recorded ...", so the tail became a bogus
   statement: `syntax error at or near "that"`. The tempting fix was to
   reword my comment — which would have hidden the bug and left it for
   whoever wrote the next semicolon. Checked instead whether psycopg
   could execute multi-statement SQL directly: it can (parameterless
   `execute()` uses the simple query protocol, and Postgres parses the
   statements itself, semicolons-in-comments included). Verified that
   empirically before relying on it. The splitter was both unnecessary
   and the bug, so it's deleted rather than patched.

2. **The spike container was broken while every host test passed.**
   `circles.py` imports the contract via a `sys.path` shim that resolves
   from the repo, so 11/11 tests passed locally — but the spike's Docker
   build context was its own directory, which doesn't contain
   `backbone/interfaces.py`. The container built fine and died at startup
   with `ModuleNotFoundError: No module named 'interfaces'`. Only caught
   by actually running `docker compose --profile spike up` rather than
   trusting green tests. Fix: build both gateway and spike-backbone from
   the repo root and copy the contract into each image, plus a root
   `.dockerignore` (a directory-scoped one is inert under a root context,
   so the two per-service files were removed rather than left looking
   active). Same class of mistake as the Week 2 event-loop issue: code
   that works in one entry point and silently doesn't in another.

3. **A passing end-to-end check that proved nothing.** The first Docker
   end-to-end run printed "bob received: satsang at 6pm" and looked like
   a success — but the body and circle id didn't match what had just been
   posted. It was leftover backlog from an earlier curl test still owed to
   `bob`, correctly redelivered by the outbox. The outbox was right; the
   *test* was meaningless. Rerun with per-run unique user ids and explicit
   assertions on the exact body, plus a non-member case. Worth recording
   because the failure mode was a green result, not a red one.

**On verifying the boundary:** the acceptance criterion asked for a grep
proving `gateway/circles.py` doesn't reference the concrete backbone. A
plain grep *fails* — the file's own docstring explains which backends it
deliberately avoids, so "matrix" and "outbox" appear as prose. Rather
than weaken the pattern until it passed, the check parses the file's AST
and inspects real imports and referenced names, ignoring comments and
strings. It reports four imports (`typing`, `fastapi`, `pydantic`,
`interfaces`) and no concrete implementation names. That's a check that
would actually catch a violation, where a tuned grep might not.

**Claude Code reliability:** the design work (interface shape, reusing
the outbox fan-out, transaction boundary for membership resolution) was
sound first time and no invented APIs appeared. The two real misses were
both *environment* rather than logic — the migration splitter inherited
from Week 2, and the Docker context assumption — and both were found by
running things rather than by reading them. Consistent with Week 2's
finding: the failures are platform-interaction gaps, not hallucinations.
One near-miss worth noting: the first draft of the non-member test poked
a private Starlette attribute (`ws._receive_queue`) that doesn't exist —
checking the actual API showed the real name is `_send_rx`, and that
prompted a better approach anyway (assert the Postgres invariant that no
delivery obligation was ever created, rather than "nothing arrived yet",
which is indistinguishable from "nothing has arrived *yet*" and would
have been quietly flaky).

## Week 4 — Matrix implementation of the circles backbone (ADR 0002 decided)

**Date:** 2026-08-25

**Prompt (summarized):** ADR 0002 has been decided in favour of Option A
(Matrix/Tuwunel) — build the Matrix-backed `CircleBackbone`
implementation. Step 0, before any code: locate the teammate's Spike A
material at one of two named paths; if missing, stop and say exactly
what's missing rather than recreate or guess at it. Then: stand up
Tuwunel (not Conduit — Spike A's confirmed unfixed join bug), implement
`MatrixCircleStore` against the same 6-route HTTP contract
`spike-custom-lite` already exposes, treating every mapping (room
creation, membership, sender attribution, encryption-off, pagination) as
a hypothesis to verify against a real server rather than a certainty.
Two decisions had to be picked and written down, not left implicit:
whether the AS impersonates the sender or posts as itself, and what
"offline member still gets it" actually means under Matrix's sync model.
Swap `BACKBONE_URL`, confirm `gateway/circles.py` needs zero changes via
an AST check, not a plain grep.

**Step 0, as it actually went:** neither named path
(`services/backbone-spike-a/` or `backbone/spike-matrix-a/`) existed.
Found the real material at a third location,
`backbone/Spike material A/services/backbone-spike-a/` — untracked by
git, a space in the directory name, one level deeper than either named
path. Read it fully before deciding anything (327-line findings doc with
real HTTP/server-log evidence, a working 149-line AS bot, 9 passing
tests) and confirmed it was genuine, substantial work, not a stub. Then
stopped and asked rather than silently relocating it: moving a
teammate's uncommitted work into "the structure I think is right" is the
same category of unilateral guess Step 0 was written to prevent, one
step removed from recreating it outright. Given "do the most feasible
thing" in response, chose `backbone/spike-matrix-a/` over the other
option on reflection — it matches ADR 0001's top-level-folder convention
and sits beside `spike-custom-lite/`, where a new top-level `services/`
would not. Preserved the original `services/backbone-spike-a/` nesting
inside that new location specifically because the findings doc's own
relative links (`../services/backbone-spike-a/app/bot.py`) depend on it —
noticed only by reading the doc's links before moving anything, not
after.

**Methodology: verify every hypothesis against a real server before
writing the code that depends on it.** Before `matrix_client.py` or
`matrix_circle_store.py` existed, ran each planned operation by hand
against a live Tuwunel container with raw `httpx` calls and read the
actual response: first-user registration and its UIA `m.login.dummy`
stage, the admin-room appservice-registration command and its confirming
reply, whether the bot auto-joins as room creator (yes), whether no
`m.room.encryption` event exists on a room created without one (yes,
404), whether a member joins cleanly via AS impersonation with no
Conduit-style join bug (yes), and the `/context` + `/messages` pagination
technique needed for the interface's `before` parameter (worked,
confirmed by anchoring mid-sequence and checking later events were
excluded). The one hypothesis that came back genuinely interesting rather
than "yes, as expected": whether a member added *after* a message can see
it. Tested directly with a brand-new user who was never present
beforehand, reading via *that user's own* impersonated access rather than
the bot's system-level read — confirmed yes, under Tuwunel's default
`history_visibility: shared`. That's the real, documented finding behind
this week's second required decision, not an assumption.

**Real bugs found, each by running the actual thing, not by review:**

1. **`psycopg`'s Windows event-loop issue from Week 3 does not recur
   here** — worth noting as a negative result: this service uses `httpx`
   throughout, not `psycopg`, so there was nothing to hit. Confirms that
   bug was specific to `psycopg`'s async driver, not a property of async
   Python on Windows generally.
2. **pytest-asyncio API mismatch.** First test run failed at fixture
   setup: a manually-defined `event_loop` fixture (the pattern used
   safely elsewhere in this codebase's Python history) conflicts with
   pytest-asyncio 1.4.0's own session-scoped runner machinery. Fixed by
   removing it entirely and using `@pytest_asyncio.fixture(scope="session",
   loop_scope="session")` plus `@pytest.mark.asyncio(loop_scope="session")`
   on each test — the modern supported shape, found by reading the actual
   `AssertionError` traceback into pytest-asyncio's own source rather than
   guessing at a fix.
3. **Two un-templated registration fields.** `AS_ID` and
   `BOT_LOCALPART`/`sender_localpart` were both configurable in Python but
   hardcoded literals in `registration.yaml` — the real server-side
   registration silently ignored both env vars. Found as two separate,
   specific failures: bootstrap's confirmation check timing out because
   the server's real reply named the hardcoded id, not the configured
   one; then `create_circle` failing with `M_EXCLUSIVE` because the code
   tried to operate as a bot user the server had never actually
   registered under that name. Neither was visible from reading the code
   — both only showed up by running a differently-configured identity
   against the real server.
4. **Test/service identity conflict, and the design lesson underneath
   it.** First design used a separate `"_test"`-suffixed admin username,
   AS id, and bot localpart for the test suite, reasoning that isolation
   from the "production" identity was safer. This broke the moment the
   test suite ran against a Tuwunel instance where the actual
   `matrix-circle-service` container had already bootstrapped: Tuwunel
   grants "first user becomes admin, auto-joined to the admin room" to
   the literal first-ever user on that homeserver instance, not to "the
   first time this particular username was registered" — a second,
   different admin identity registers as an ordinary user with zero
   special rooms and there is no API to discover or join the admin room
   after the fact. The fix was not a workaround but a correction to the
   design itself: there is only one true bootstrap-admin path per
   homeserver, so the tests and the running service registering the
   *same* appservice identity isn't a compromise, it's the accurate
   model — confirmed by removing the test-specific overrides and running
   the suite three times in a row against an already-bootstrapped
   instance without conflict.
5. **Tuwunel's own documentation is wrong about one thing.** Its
   published appservices page says re-registering an existing appservice
   id "replaces the previous entry." Empirically false for the version
   deployed here: a second registration of the same id gets `"Failed to
   register appservice: Duplicate id: <id>"`. Found by actually re-running
   bootstrap twice against a live server and reading the real reply — not
   by re-reading the docs more carefully, which would not have caught it,
   since the docs are simply incorrect. `bootstrap.py` now treats that
   specific error as an equally-valid confirmation, and says so in both
   the module docstring and inline at the check itself, since trusting
   vendor documentation over an empirical result would be exactly the
   habit this whole project has been pushing against.
6. **Bootstrap's admin-room discovery has a real, undismissed limitation**,
   surfaced by finding #4 above before the fix: it only works cleanly
   against a genuinely fresh Tuwunel volume. Documented in
   `bootstrap.py`'s `_find_admin_room` with the concrete recovery step
   (`docker compose --profile matrix down -v tuwunel matrix-circle-service`)
   rather than built around with a more complex fallback — real,
   out-of-scope work (tracking whether bootstrap already ran, e.g. via a
   marker on the data volume) that a spike doesn't need to solve to prove
   the mechanism.

**The two required decisions, documented in
`matrix_circle_store.py`'s module docstring** (repeated in
`docs/adr/0002-chat-backbone.md`'s Week 4 section, not duplicated in full
here): the bot posts every announcement as itself with the real sender
recorded in a custom content field, because the interface's own contract
says the sender need not be a member and impersonation can't satisfy that
without corrupting `list_members` or failing outright; and offline
delivery under Matrix means something qualitatively different from the
outbox's per-recipient obligation — a member added after a post can still
see it, verified directly rather than assumed to work "the same way".

**Verification, shown in full during the session, not summarized:**
`docker compose up --build` (no profile) reconfirmed healthy after every
change that touched shared files (the `.dockerignore`/build-context
changes, the `docker-compose.yml` `BACKBONE_URL` default change) — twice,
including one clean rebuild from scratch after the default changed. AST
check (not grep — a plain grep false-positives on this file's own
docstring prose) confirmed `gateway/circles.py` imports nothing beyond
`interfaces`, `fastapi`, `pydantic`, `typing`. `gateway/`'s existing 9
tests pass unmodified. All 4 required Matrix circle behaviours pass
against real Tuwunel, run three times in a row for stability including
once sharing identity with the live `matrix-circle-service` container.
Full end-to-end proof run through Caddy → gateway → matrix-circle-service
→ Tuwunel, with the real (not manually overridden) `BACKBONE_URL`
default, using freshly-generated user/circle names each run so leftover
state from earlier verification couldn't produce a false pass (the same
discipline Week 3's journal entry flagged after a similar near-miss) —
including one deliberate check that a room id returned by the gateway
actually starts with `!`, i.e. is a real Matrix room, not a
coincidentally-similar Postgres identifier.

## Week 4 — wire the elder client to the gateway, prepare staging deploy

**Date:** 2026-08-29

**Prompt (summarized):** Wire a Reflex elder client to the gateway
end-to-end (connect/send/receive/reconnect-with-backoff over WebSocket),
move auth off the Week 1 caller-asserted-string stub, configure CORS
explicitly (not `"*"`), and verify locally with two browser tabs plus a
mid-session gateway kill. Then prepare a staging deploy to a remote
Ubuntu host reachable by raw IP (no domain yet, so plain HTTP -- Caddy
can't issue a cert for a bare IP): a deploy script, `docs/deployment.md`,
firewall scoped to only the ports actually needed, and a gitignored notes
file for the real IP. Explicit requirement: show real connect/reconnect
logs, not a summary that it "works."

**Step 0: no Reflex client existed yet.** Member 1's UI shell hadn't
landed under `client/`, `app/`, or anywhere else in the tree as of this
session. Built a placeholder instead, `clients/elder-app/` -- a bare
Reflex page (name input, message list, send box) whose own on-page
subtitle literally says "Placeholder test client (Week 4 WebSocket
wiring proof) -- not the real UI shell," and whose module docstring says
the same, so nobody mistakes it for the deliverable it's proving the
wiring for.

**The auth design decision, in `gateway/auth.py`'s own module
docstring** (not repeated in full here): HMAC-SHA256 signed tokens,
`base64url(user_id:expiry) "." hex(hmac)`, deliberately not JWT -- a full
JWT library is more machinery than a two-field signed token needs for
what this week actually requires. A client calls `POST /session` once
with a display name, gets back a token bound to a sanitized user_id, and
`gateway/ws.py` verifies the signature and expiry on every WebSocket
connection, closing with code `4401` (not the generic 1006 a network drop
produces) before `accept()` if it fails -- an unauthenticated socket never
sees application traffic, not even briefly. Still not the final auth
system: no password, nothing stops someone else from claiming "bob" first
if "bob" hasn't claimed it. What it does fix, precisely per this week's
scope: a message's `sender_id` is no longer a bare string the client
asserts fresh on every message.

**The delivery-architecture decision, in `gateway/ws.py`'s module
docstring** (not repeated in full here): `CircleBackbone` has no
push/subscribe method, and adding one was rejected as bigger than this
week's scope (every backbone implementation would need a matching
subscribe mechanism). Delivery is instead two honestly-separate things --
durable history through the existing pull-based interface, and live push
through a gateway-local, in-memory `ConnectionRegistry` that does not
survive a gateway restart or scale past one instance. Named explicitly as
a limitation, not discovered the hard way later -- same shape as
spike-custom-lite's dispatcher limitation from Week 2/3.

**CORS**, per the task's own warning that skipping it "will fail silently
as a blocked browser request": `gateway/main.py` reads `ALLOWED_ORIGINS`
(comma-separated) into `CORSMiddleware`'s `allow_origins`, never `"*"`.
Caught one real instance of exactly the warned-about failure mode before
it shipped: a bare `reflex run` dev server on `:3000` talking to the
dockerized gateway on `:80` is genuinely cross-origin, and the first
attempt without `:3000` listed produced a silently-blocked request with
zero gateway log entry -- confirms the task's own framing that this fails
invisibly, not with an obvious error.

**Three real attempts before script execution actually worked, in
`elder_app.py`'s own module docstring** (fuller technical detail there,
summarized here): `rx.script(...)` crashes via `react-helmet`
(`Cannot read properties of null (reading 'addEventListener')`, confirmed
by reading the compiled `.web/app/routes/_index.jsx` and finding Helmet
wrapping the script tag) -- switched to `rx.el.script(...)` (both inline
and as an external `src=` file) to bypass Helmet, and neither ever
executed at all in this React/Vite combination, confirmed by direct
`.click()` testing producing zero output and, for the external-file
variant, `read_network_requests` showing the browser never even issued
the request for the file despite it being correctly served. The working
fix: `on_mount=rx.call_script(CLIENT_JS)` on the root element -- verified
before writing it, not guessed, by reading `reflex_base`'s own
`on_mount`-to-`useEffect` compilation code and `rx.call_script`'s
docstring. A second, smaller bug in the same area: `rxconfig.py`
originally pointed Reflex's own internal `api_url` (its state-sync
backend, unrelated to the SatSandesh gateway) at the gateway's URL,
producing a `Connection Error` toast and a second, different
`addEventListener` crash from React's own dev tooling -- fixed by never
overriding `api_url` for local dev, only via a distinctly-named
`REFLEX_API_URL` env var for the containerized deployment.

**Reconnection**: exponential backoff with jitter (1000ms base, doubling,
capped at 30000ms, `Math.random() * 300` jitter), and a distinction the
client makes deliberately: `WebSocketDisconnect`/network-drop codes
retry, but the auth-rejection close code (4401) does not -- retrying a
connection whose token was rejected would just spin forever.

**Verification, real logs captured directly, not summarized (see the
raw console output pulled from the browser during this session for the
exact lines):**

Two tabs joined as `alice` and `bob` against the dockerized stack (base
services + `matrix` profile, Caddy on `:80`, elder-app via local `reflex
run` on `:3000` -- genuinely cross-origin, exercising real CORS, same as
the auth-design verification above). Alice sent "hello from alice", Bob's
tab rendered it live within the same second; Bob replied, Alice's tab
rendered that. Server-side gateway logs corroborated each step
(`POST /session 200 OK`, `WebSocket /ws?token=... [accepted]`,
`connection open`).

One genuine, minor finding along the way, not a defect in this week's
code: both `alice`'s and `bob`'s connect-time `add_member` call to
`matrix-circle-service` returned a transient 503 (confirmed transient --
an immediate manual retry of the identical call succeeded), so both
clients displayed the "messages will not be saved" warning built for
exactly this case. But `post_announcement` for each of their actual
chat messages succeeded independently (`200 OK` in
`matrix-circle-service`'s own log) -- the messages WERE durably saved,
despite the earlier warning saying otherwise. Not fixed under this week's
scope (Matrix backbone flakiness is ADR 0002 territory, not "wire a
client" territory); noted here because a real UI would want a
per-message ack rather than a static connect-time banner, which is a
fair thing for Member 1's real shell to consider rather than something
this placeholder needs to solve.

**The kill/reconnect test, the one the task said to actually show, not
summarize:**

```
21:17:31.751  docker kill satsandesh-gateway-1
```

Bob's tab, real console output:
```
[satsandesh-ws] closed, code=1006 reason=
[satsandesh-ws] disconnected -- reconnect attempt 1 in 1042ms
[satsandesh-ws] disconnected -- reconnect attempt 2 in 2166ms
[satsandesh-ws] disconnected -- reconnect attempt 3 in 4053ms
[satsandesh-ws] disconnected -- reconnect attempt 4 in 8132ms
[satsandesh-ws] connected
[satsandesh-ws] status: Connected as bob
```

Alice's tab, independently, real console output:
```
[satsandesh-ws] closed, code=1006 reason=
[satsandesh-ws] disconnected -- reconnect attempt 1 in 1279ms
[satsandesh-ws] disconnected -- reconnect attempt 2 in 2240ms
[satsandesh-ws] disconnected -- reconnect attempt 3 in 4188ms
[satsandesh-ws] disconnected -- reconnect attempt 4 in 8199ms
[satsandesh-ws] connected
[satsandesh-ws] status: Connected as alice
```

```
21:17:44.811  docker start satsandesh-gateway-1
21:17:45.386  (container started)
```

Both tabs independently succeeded on their 4th attempt, ~15-16s after the
kill (matching container restart + Caddy's own upstream retry, not a
client-side timing coincidence -- the two tabs' backoff schedules are
close but not identical, since jitter is independently randomized per
client). Both landed in a fresh circle (`!El4FYFRyAegwagmtAC:localhost`,
different from the pre-kill circle) -- the documented "live registry and
default-circle memoization don't survive a gateway restart" limitation
from `gateway/ws.py`'s own docstring, now empirically confirmed rather
than only designed-for. Zero page reloads on either tab. Alice then sent
"back online, alice here" and Bob's tab rendered it live, confirming the
reconnected sockets are fully functional, not just technically open.

**Docker build blocker for `clients/elder-app/Dockerfile`, real and
still only partially resolved:** `RUN reflex init` (which bootstraps
`bun` and installs frontend deps at build time) fails with `SSL:
CERTIFICATE_VERIFY_FAILED / unable to get local issuer certificate` on
this dev machine's network -- the same class of symptom this session
already hit with `git` itself earlier (worked around there via a
different, Windows-specific mechanism). Traced to two separate downloads
inside that one step failing the same way: Reflex's own fetch of bun's
install script from `raw.githubusercontent.com` (fixed with
`SSL_NO_VERIFY=1`, an env var Reflex's own downloader respects), and that
installed script's own subsequent plain `curl` call for the actual `bun`
binary from `github.com/oven-sh/bun`, which does not read that variable
and needed a scoped, removed-in-the-same-layer `~/.curlrc` with
`insecure` to get past. This is a real security downgrade (build-time
MITM risk) and the session's own permission system correctly declined to
let it run the second, wider version of that fix automatically -- the fix
is left in the Dockerfile, documented inline as scoped to this one step
and likely specific to this machine's network (not the app), but the
actual `docker compose build elder-app` has NOT been verified to succeed
end-to-end from this session as a result. **All local verification above
used a bare `reflex run` dev server talking to the dockerized gateway,
not a containerized elder-app** -- the containerized version of the exact
same code is the one piece of Step 1 not yet proven, and needs a human
(or a differently-configured environment) to actually run the build once
to confirm.

**Step 2 (staging deployment): blocked on server access, not attempted.**
Asked directly rather than guessing or fabricating a deployment; the
answer was that server access is uncertain -- a shared college server
exists for the group project but isn't currently being used, and there's
no confirmed reachable host with credentials in hand right now. Per
"do the most feasible thing," wrote `infra/deploy/deploy.sh` and
`docs/deployment.md` as a real, executable plan (Docker install, `ufw`
scoped to ports 22+80 only -- deliberately not opening the dev-convenience
host ports 5432/8008/8101 that `docker-compose.yml` publishes for local
poking -- `docker compose --profile matrix up -d`, healthcheck polling)
rather than placeholders, but the actual "two tabs from two devices
reach `http://<server-ip>`" acceptance test has NOT been run against a
real remote host. `docs/deployment.md` says so explicitly at the top
rather than implying otherwise.

**What's still a placeholder, stated plainly:**

- The client UI itself -- `clients/elder-app/` is deliberately minimal
  and explicitly labeled as such, pending Member 1's real Reflex shell.
- HTTPS -- deferred on purpose until a domain exists, required before
  Week 6-7 for microphone access (browsers block `getUserMedia` on
  non-HTTPS origins except `localhost`). Documented in
  `docs/deployment.md`.
- The containerized build of `clients/elder-app` -- written, and its
  known SSL blocker documented and partially worked around, but not
  confirmed to actually build successfully in this session.
- Remote staging deployment itself -- script and docs are real and
  executable, not yet run against a real server.

## Week 4 (continued) — real staging deployment, done live over chat

**Date:** 2026-08-30

Server access arrived: a directory on the shared college server
(`cybersecurity`, user `satsandesh`). Ran the actual Step 2 deployment
live, one command at a time relayed through chat (no direct terminal
access to that host from this session) -- everything below is what
genuinely happened, including several real problems neither the plan nor
the local testing had surfaced.

**The 6 pending commits from earlier this week had never been pushed.**
First real blocker: `git clone` on the server pulled a version of the
repo missing all of this week's work (auth, CORS, the elder client, the
deploy script itself). `git push` had simply never been run after any of
the local commits. Pushed all 6 before anything else could proceed --
worth calling out plainly since it's an easy mistake (local commits feel
"done") and wasted a full round-trip of debugging a phantom `.env.example`
before the real cause was obvious.

**No `sudo` on this account.** `infra/deploy/deploy.sh`'s firewall setup
needs root and can't run here. Skipped it and ran the underlying `docker
compose` commands directly -- Docker itself didn't need `sudo` (the
account was already in the `docker` group), so this only cost the
firewall-hardening step, not the deployment itself.

**Two host-port conflicts, both from other real things already running
on this shared server:** a teammate's own Postgres container already had
`5432`, and a system-level Apache already had `80`. Neither was
hypothetical -- both showed up as actual bind failures. Fixed properly
rather than worked around per-deploy: made both ports configurable via
env vars (`POSTGRES_HOST_PORT`, `CADDY_HOST_PORT`, both defaulting to the
standard port so nothing changes for anyone not on a conflicting host) --
see `docker-compose.yml`. Remapped to `55432` and `8080` here.

**The server's IP is not actually public.** The IP originally given
(`10.110.11.31`) turned out to be a private/internal address; `curl
ifconfig.me` from inside the server returned a *different* address
(`14.139.86.54`), which turned out to be the campus network's shared
outbound NAT gateway, not an address that routes back to this specific
machine -- confirmed by testing port 80 (already serving Apache
successfully to `curl` from *inside* the server) from outside and having
it fail to load entirely, same as the app's own port. Nothing on the box
itself can fix this; it needs the network administrator to configure
inbound port-forwarding if genuine public-internet reachability is
required. Tested reachability from the private address instead
(`10.110.11.31:8080`), confirmed working from a device on the same
campus network -- the acceptance test below was run over the campus
network, not the open internet, as a direct consequence.

**`clients/elder-app`'s Docker build succeeded cleanly here** -- no SSL
certificate error at all, confirming the earlier local-machine build
blocker really was specific to that dev machine's network (likely a
TLS-intercepting proxy/antivirus), not the app or the Dockerfile.

**A real, previously-undiscovered bug: `--env prod`'s backend never
started.** The container reported healthy build, the frontend came up
and served pages, but `/ping` on port 8000 stayed connection-refused
indefinitely -- confirmed via the healthcheck's own repeated
`ConnectionRefusedError` log and, more directly, by listing processes
inside the container via `/proc` (no `ps` binary in the slim image) and
finding only the single Reflex CLI process, no separate backend process
at all. This Dockerfile's `CMD` had used `--env prod`, which was never
actually tested locally -- all prior verification used `--env dev`. Fixed
by switching the `CMD` to `--env dev`, the mode with real, verified
evidence behind it, rather than continuing to debug an unverified
prod-mode code path under time pressure. Documented as a real gap: dev
mode's hot-reload overhead isn't what a genuine production deploy should
want, and the actual root cause of prod mode's backend not binding is
still unknown.

**A second real CORS/origin mismatch, caught by testing, not guessed:**
`.env` was initially set up with `ALLOWED_ORIGINS`/`PUBLIC_ORIGIN`
pointing at the NAT gateway address (`14.139.86.54:8080`) before the
private-address discovery above. The browser's real `Origin` header when
visiting the working address (`10.110.11.31:8080`) didn't match, and the
client surfaced this honestly as a connection-timeout error naming the
exact mismatched WebSocket URL it was trying to reach -- exactly the
"fails silently as a blocked request" failure mode this project's CORS
design already anticipated, just from a different cause (wrong configured
origin, not a missing one) than the Week 4 auth-work entry's example.
Fixed by correcting both values to the actual working address and
force-recreating the two containers that read them (`gateway`,
`elder-app`) -- `docker compose restart` would NOT have picked up the
`.env` change; env vars are only read at container creation.

**`docker kill` does not trigger `restart: unless-stopped`.** Killed the
gateway to test recovery, and it stayed `Exited (137)` indefinitely --
Docker treats a manual `kill` the same as a manual `stop` for
restart-policy purposes, so the policy never fired. Not a bug: the
assignment's own wording ("kill **and restart** the gateway container")
already implies both steps are manual, and a `docker start` afterward is
exactly the intended test, not a workaround for a broken policy.

**Verification, real, from the actual deployed stack:**

Three different named users (`vk`, `kpsp`, `dongre`) joined and exchanged
live messages visible to each other in the same session. Then, with one
tab connected and left untouched, `docker kill veerendra-gateway-1` was
run -- the tab's own status line changed to reflect the drop without any
reload, then `docker start veerendra-gateway-1` brought the gateway back
and the same tab recovered to "Connected as ..." on its own, matching
exactly the local reconnect behavior already verified with full logs
earlier this week (same client code, unchanged).

**What's still genuinely open:**

- Public-internet reachability (as opposed to campus-network
  reachability) needs the college server's network administrator to set
  up inbound port-forwarding -- outside what either this session or the
  account's own permissions can do. A ready-to-send request for that is
  available if/when it's needed.
- `--env prod`'s backend-not-starting issue is unresolved, only worked
  around by using `--env dev` instead.
- HTTPS remains deferred on purpose, per the original plan, until a real
  domain exists.
