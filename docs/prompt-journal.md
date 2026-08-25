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
