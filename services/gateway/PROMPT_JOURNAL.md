# Prompt Journal — services/gateway/, one session

Covers the session that built the gateway skeleton, health checks, auth
stubs, the WebSocket echo endpoint, and typed configuration, in that order.
Written for a report on AI-assisted engineering — corrections and mistakes
are recorded here deliberately, not smoothed over.

Note: the skeleton phase happened in an earlier session, not this one — it's
referenced below for context but not detailed, since it isn't part of this
session's transcript.

## Phase: skeleton (prior session, referenced only)

Not part of this session's transcript in detail. `app/main.py` and the
FastAPI app object already existed at the start; this session's phases build
on top of it.

## Phase: health check

**Asked for:** `GET /health` (liveness, zero dependencies, instant) and
`GET /health/ready` (readiness, `{"status": ..., "checks": {...}}`), tests
first, with a specific test proving `/health` cannot perform network/DB I/O.

**Produced:** `tests/test_health.py` with three tests, then `app/main.py`
routes. First draft of the dependency-free test used a module-level
`client = TestClient(app)` and monkeypatched `socket.socket.connect` globally
inside the test.

**What was wrong:** on this Windows/Git Bash setup, `TestClient` used outside
a `with` block builds a fresh `anyio` portal — and the OS-level socketpair
that requires — on every single request. The global socket patch broke that
internal plumbing, so the test failed with `AssertionError: /health must not
perform network or socket I/O` even before any route existed, for a reason
that had nothing to do with the handler under test. Caught by inspecting the
first failing-test run before implementing anything, per the tests-first
workflow the user required. Fixed by switching to a `client` fixture that
enters `TestClient(app)` as a context manager (which reuses one portal across
requests) before applying the monkeypatch, so the patch only catches I/O the
handler itself attempts.

**Decided differently:** none — the fix was accepted as proposed once
explained.

## Phase: auth stubs

**Asked for:** `app/models.py` (`User`), `app/auth.py` (`get_current_user`
stub, `require_role` factory), wired into `/me` and a demo
`require_role("moderator")` route. Explicit constraint: no real JWT
verification, no password handling, no user database — a correctly-shaped
seam for someone else to fill in next week.

**Produced:** tests first (`tests/test_auth.py`), confirmed failing on
`ModuleNotFoundError` (module didn't exist yet), then the models/auth/route
implementation.

**What was wrong:** `ruff check` flagged `B008` (flake8-bugbear) on every
`Depends(...)`/`require_role(...)` default — five errors. This is a known
false positive against FastAPI's own dependency-injection idiom, not a real
defect; rewriting the DI pattern to dodge the linter would have made the code
worse. Fixed via ruff's documented allowlist
(`tool.ruff.lint.flake8-bugbear.extend-immutable-calls`) in `pyproject.toml`
rather than restructuring the routes.

**Decided differently:** none on substance. The user did ask, afterward, for
the DI reasoning to be explained back rather than just asserted — a check on
understanding, not a code correction.

## Phase: WebSocket echo — the reject-path finding

**Asked for:** `/ws` route, token via `?token=` query param (browsers can't
set custom headers on a WS handshake), reusing `get_current_user`'s logic
rather than duplicating it, `ConnectionManager` (in-memory,
`dict[user_id, set[WebSocket]]`), disconnects treated as normal control flow.
Tests first, as with the earlier phases.

**Produced:** `app/auth.py` refactored to extract `user_from_token(token)` as
the single stub implementation shared by `get_current_user` (HTTP,
Authorization header) and the new WS route (query param) — one auth
implementation, not two. `app/ws.py` with the route, manager, and a
close-before-accept rejection sending code 1008 for a missing token.
`tests/test_ws.py` asserted `WebSocketDisconnect` with `code == 1008` on the
missing-token path, and passed.

### Finding 1 — the test asserted 1008; a real browser saw 1006

The user manually verified in a real browser (explicitly requested, because
`TestClient` never exercises an actual HTTP upgrade handshake) and reported a
disagreement: the reject-path test passed asserting `1008`, but the browser
console showed `Error during WebSocket handshake: Unexpected response code:
403` followed by `CLOSED 1006`.

**Root cause, confirmed by reading the installed package source, not just
asserted:**

- `app/ws.py` called `websocket.close(code=1008)` without ever calling
  `websocket.accept()` first.
- Starlette's `WebSocket.close()` just forwards `{"type": "websocket.close",
  "code": 1008, ...}` as a raw ASGI send; its state machine allows a close
  before an accept with no special handling of the code.
- Uvicorn is what actually terminates the connection at that point. Its
  `asgi_send` handler
  (`uvicorn/protocols/websockets/websockets_impl.py`), when the app's first
  send is `websocket.close` (handshake never started), hardcodes the HTTP
  response to `403 Forbidden` and never reads `message["code"]` — the 1008 is
  discarded, not transmitted anywhere. This is structural: RFC 6455 close
  frames only exist after the `101 Switching Protocols` handshake completes,
  and since `accept()` was never called, uvicorn can only reject at the plain
  HTTP layer.
- A real browser reports any handshake-layer failure (non-101 response) as
  `onerror` then `onclose` with code `1006` — the spec-mandated generic
  "abnormal closure" code, which deliberately hides the real HTTP status from
  JS.
- `TestClient`, by contrast, reads the raw ASGI message stream in-process
  (`message.get("code", 1000)` in `starlette/testclient.py`) rather than
  going over a wire protocol, so it faithfully reported the `1008` that was
  never actually deliverable to any real client. The test was asserting on an
  ASGI-internal value, not observable behavior.

**Why it matters:** the user's Week 3 task is offline-queue/reconnect logic
for elders on unreliable rural mobile networks. Reconnect logic needs to be
able to tell "your token is bad, stop retrying and re-authenticate" (1008)
apart from "the network dropped, keep retrying with backoff" (1006). Under
the original close-before-accept design, both cases were indistinguishable
1006 to any real client — a client with an expired token would retry forever
against a server that would never accept it.

**Decided differently — by the user, with two options presented:** the user
was given the trade-off directly (reject pre-accept, always ambiguous 1006;
vs. accept-then-close(1008) with a reason, briefly accepting an
unauthenticated socket) and chose accept()-then-close(1008, reason=
`"missing_or_invalid_token"`) explicitly because of the Week 3 reconnect
requirement — not a default the assistant picked unprompted. Implementation
followed only after that explicit confirmation; the test was rewritten to
assert what a real client observes (the `with` block now enters successfully,
since the handshake completes, and the disconnect+code+reason surface on the
first `receive()` instead of on connect). Re-verified against a real browser
afterward: `CLOSED 1008 missing_or_invalid_token`, matching the test.

This was caught **only** by manual browser verification. Nothing in the
automated test suite would have surfaced it — `TestClient`'s in-process ASGI
transport cannot see the difference between a real close frame and an
HTTP-layer handshake rejection, because it doesn't use HTTP at all.

### Finding 2 — a stray uvicorn process outlived a Git Bash kill

While verifying `/ws` in a real browser, a server was deliberately left
running across turns (`.venv/Scripts/python.exe -m uvicorn ...` launched via
Git Bash's `&`, reported by the shell as job PID `467`). On the next turn,
the mandated pre-boot port check (`Get-NetTCPConnection -LocalPort 8000` in
PowerShell, run per the user's standing instruction to verify port 8000
before any boot check) found the port still held by an actively **listening**
process — under OS PID `11024`, not `467`.

**Root cause:** on this Windows/Git Bash setup, the PID a background job
reports via `$!` is not reliably the same number Windows/PowerShell will
report for the same underlying process. A `kill 467` issued from Bash — had
one been attempted, or had this been trusted as "the server is stopped"
without an independent check — would very likely not have terminated PID
`11024`, the process actually holding the socket. The port-check step run
that turn used PowerShell's process list to find and terminate the real PID
directly (`Stop-Process -Id 11024`), not Bash's job tracking.

No incorrect "boot check passed" was actually reported to the user in this
session — the stray listener was caught and killed before the next boot
attempt, because the port-was-verified-free step was mandatory before every
boot check, per explicit instruction given at the start of this session. But
the underlying condition — a Bash-reported PID diverging from the real
Windows-level PID holding a port — is exactly the failure mode that
instruction exists to prevent: without an OS-level check independent of
Bash's own job control, a later boot check could silently validate a
leftover process from an earlier turn (running old code) rather than the
process just started.

**This isn't hypothetical — it already happened once, in the earlier
skeleton-phase session.** A boot check there reported success, but it was
reaching a stray uvicorn process left running from a previous run, not the
freshly rebuilt venv the check was meant to verify. It was only caught the
following turn, when a port check found the leftover listener still on port
8000. The check had gone green; the thing it was actually talking to was
stale. This session had no repeat of that, specifically because the port
check was made mandatory before every boot check as a direct result. The
general point, stated plainly: a passing check proves the thing you
measured, not the thing you wanted — the discipline is to ask what a check
actually exercised, not just whether it went green.

**Decided differently:** none required a decision from the user — this is a
process-hygiene fact recorded here because the user asked for it explicitly
as a named finding, not because it changed any code.

## Phase: settings

**Asked for:** `app/config.py` (`pydantic-settings`), required
`DATABASE_URL`/`JWT_SECRET` with no default, fail loudly at startup (not
lazily at request time) if either is missing, `.env`/`.env.example`, wired
into `app/main.py` (log level, CORS), README note on Docker service-name
networking. Tests first for the loud-failure behavior specifically.

**Produced:** `tests/test_config.py` (missing-required-vars test using
`_env_file=None` + `monkeypatch.delenv` to isolate from the real developer
`.env` sitting in the same directory), confirmed failing on
`ModuleNotFoundError`, then `app/config.py`.

**What was wrong:** the `.env.example` draft documented `CORS_ORIGINS` as
comma-separated (`http://a.com,http://b.com`), which is the natural format to
hand-write. `pydantic-settings` defaults to JSON-decoding list-typed env
vars, so a comma-separated value raised `SettingsError` /
`json.decoder.JSONDecodeError` at construction — confirmed by running it
directly, not assumed. Fixed with the documented `NoDecode` +
`BeforeValidator` pattern so the field parses the comma-separated form the
`.env.example` comment actually promises.

**Decided differently:** none on substance — caught before it reached the
user as a claim, verified with a direct interpreter check before writing the
fix.

Separately, `get_settings()` is called at **module level** in `app/main.py`
(import time), not inside a request handler — this is what makes "fail
loudly at startup" true rather than "fail loudly the first time some route
needs config." Verified directly: booted the real app with `.env` present
(200 on `/health`), then renamed `.env` away and re-ran the same boot
command, which crashed on import with a `pydantic_core.ValidationError`
listing both missing fields by name, exit code 1 — not a silent partial
start.

## Phase: database layer — models, first migration, resumed session

**Asked for:** this phase spanned two sessions. An earlier, restarted
session had already produced `app/id.py` (a hand-rolled UUIDv7 generator,
since this repo pins Python 3.11 and `uuid.uuid7()` doesn't land until
3.14), `app/db/base.py`, `app/db/models.py` (SQLAlchemy models for `users`,
`circles`, `memberships`, `conversations`, `messages` matching
`docs/SCHEMA_DRAFT.md`), and `tests/test_models.py` written test-first
against that schema — but was blocked on Docker not being installed, so
none of the DB-dependent tests had ever actually run. This session's brief
was: confirm that state matches expectations before touching anything
(STEP 0), then — since Docker Desktop was now installed — start Postgres,
generate and verify the first Alembic migration, create a test database,
migrate both, run the full suite, and document the workflow in the README
(STEP 1), without committing anything.

**Produced:** STEP 0 was read-only verification — `git status`, reading
every named file in full, and running both test commands the user
specified — before any docker or alembic command ran. That confirmed the
57 pre-existing tests still passed, `test_models.py`'s one DB-independent
test (`test_uuid7_helper_is_time_ordered`) passed, and its other 7 tests
failed with `OperationalError: connection to server ... Connection
refused`, exactly the expected blocked state. Only after reporting that
back did STEP 1 begin.

**What was wrong — Docker was assumed running but wasn't, caught before
acting on the assumption:** the instructions stated Docker Desktop was
"confirmed via `docker version` showing both a Client and Server section."
Running that command first, rather than trusting the description, showed a
`Client` section only, then an error: `failed to connect to the docker API
at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the
path specified`. Checking further (`Get-Process 'Docker Desktop'`,
`Get-Service com.docker.service`, a process list filtered on `docker`) found
no Docker Desktop process and no service running at all — the engine wasn't
starting slowly, it wasn't running. Rather than guessing at a fix (or
silently running `docker run` against a context that might not resolve),
this was surfaced directly and the user was asked how to proceed: start it
themselves, have it launched on their behalf, or stop the docker-dependent
work entirely. The user chose to have it launched. `Start-Process` on
`Docker Desktop.exe`, followed by polling `docker version` every 5 seconds,
got the engine responding (`Server: Docker Desktop 4.87.0`) in about 10
seconds, and STEP 1 proceeded from there as instructed — the container, the
migration, both databases, and the full suite all completed cleanly on the
first attempt.

**Also caught before calling the phase done — a stale `.env`:** `.env`'s
existing `DATABASE_URL`
(`postgresql://satsandesh:satsandesh@localhost:5432/satsandesh_dev`) was a
Week 1 placeholder — its own trailing comment said "not yet used" — and
didn't match the credentials the instructed `docker run` command actually
creates (`POSTGRES_DB=satsandesh`, the default `postgres` superuser,
password `devpass`; no `satsandesh` user or `satsandesh_dev` database exists
in that container). Left alone, `alembic revision --autogenerate` and every
later `alembic upgrade head` would have failed connecting to a database that
doesn't exist. Fixed by pointing `.env` at the container's real credentials
(`postgresql://postgres:devpass@localhost:5432/satsandesh`) before
generating the migration — `.env` is gitignored, so this didn't touch
version control — and flagged to the user in the phase report rather than
left as a silent, unexplained deviation from the literal instructions.

**Decided differently:** none on the migration content itself. Autogenerate
is documented as unreliable at capturing `CHECK` constraints on some
SQLAlchemy/Alembic version combinations, so the generated migration file was
read in full against the explicit checklist (every `CHECK`, the partial
index, the `(author_id, client_msg_id)` `UNIQUE`, every FK's `ON DELETE`
clause) before treating it as done. All of it came through correctly on the
first generation; no hand-editing was needed.

**Follow-up, same phase:** after the initial report, the user asked for
three more things before committing: sync `.env.example`'s `DATABASE_URL`
to the same real credentials (so the next person copying it doesn't hit the
same connection-refused confusion), fix the two README lines flagged as
stale in that report ("Out of scope this week: ... a database", and "12
tests across 4 files" — now 65 across 12), and this journal entry. All
three were diffed for the user before being asked to commit; nothing in
this phase was committed by the assistant at any point, per standing
instruction.

## Known issue, flagged Week 3 Phase 7 — real boot can't resolve `contracts/`, pytest can

Recreating `.venv` from scratch (required to get real pinned versions for
`pywebpush` into `requirements.txt`, per the documented regenerate process)
surfaced a **pre-existing** gap, not something this phase's changes caused:
`python -m uvicorn app.main:app` fails with `ModuleNotFoundError: No module
named 'contracts'`, since `contracts/` lives at the repo root, outside
`services/gateway/`, and nothing in this package's config makes it
importable for a direct interpreter/uvicorn invocation. `pytest` itself is
unaffected — confirmed all 118 tests still pass, and empirically confirmed
the repo root lands on `sys.path` during a pytest run by other means pytest
doesn't fully explain in the time spent on it, so it wasn't chased further.
`alembic` is also unaffected (`alembic/env.py` only imports
`app.config`/`app.db.base`/`app.db.models`, none of which touch
`contracts`). Confirmed with the user this is a real, separate fix — not
folded into push-notification logic — to be done as its own small step
right before Phase 7's real-browser verification, since that step needs an
actual running server, not just pytest.

**Resolved, Step 3 (real-browser verification prep):** `PYTHONPATH=../..`
on the direct `uvicorn` invocation, documented in README.md's Quickstart —
not a new packaging scheme (turning `contracts/` into an installable
package) and not a `sys.path` hack living in application code. Chosen
specifically because `services/ai/README.md` already documents this exact
convention (`PYTHONPATH=../.. ./.venv/Scripts/python.exe
tools/generate_fixtures.py`) for the identical problem — this service's
`app/ws.py`/`app/messages.py`/`app/push.py` importing `contracts/` outside
of pytest, which has its own `conftest.py`-based `sys.path` insertion that
only ever applies to test runs. Matching the sibling service's established
answer beats inventing a second way to solve the same problem. Tradeoff:
this has to be remembered on every direct interpreter/uvicorn invocation
(easy to forget, would surface immediately as `ModuleNotFoundError:
No module named 'contracts'` if it is) — a real packaging fix (installing
`contracts/` as an editable dependency) would make it automatic, but that's
a bigger, cross-cutting change touching `services/ai/` too, out of scope
for this one-service fix.

Verified directly, not assumed: booted `python -m uvicorn app.main:app`
for real from `services/gateway/` with `PYTHONPATH=../..` set (PowerShell,
`Start-Process` with `-PassThru` so the returned PID is the real Windows
PID directly — no Bash-job-vs-OS-PID mismatch risk this time, per Finding 2
above). `GET /health` returned `{"status":"ok"}` and `GET /health/ready`
returned `{"status":"ok","checks":{"postgres":"ok"}}` — the latter proves
every router (including the ones that import `contracts/` at module level:
`app/ws.py`, `app/messages.py`, `app/push.py`) loaded cleanly, not just
`/health`'s own dependency-free path. Process stopped via `Stop-Process`
on that same confirmed PID; a follow-up `Get-NetTCPConnection -LocalPort
8000` came back empty, confirming port 8000 was actually released rather
than trusting the stop command's own exit code.

## Phase: job queue, retention sweeper, end-to-end proof (Week 6, continued)

**Asked for:** a durable job queue (tests first, prove a queued job
survives a killed-and-restarted process — "notes survive a saturated
CPU"), then, in a later prompt: an audio retention sweeper enforcing a
real 30-day window (row and bytes both actually deleted, tested with an
artificially aged artifact), a real end-to-end proof of a media upload
flowing through the queue to `services/ai/mock/` (never real ASR), the
Month-1 exit-gate smoke path (1:1 message, circle post, announcement)
re-run against this week's schema changes, and honest documentation of
what was guessed.

**Produced:** `app/db/models.py::Job`, `app/db/repository.py`'s
`enqueue_job`/`claim_next_job`/`complete_job`/`fail_job`/
`compute_backoff_seconds`, `app/jobs.py`'s worker loop, wired into a new
`app/main.py` lifespan (this app had none before). Then
`app/retention.py`'s sweep, on its own timer in the same lifespan. Then
`app/jobs.py::_handle_transcribe_media`, enqueued from
`app/media.py::upload_media` for every genuinely new upload. Tests first
throughout — confirmed failing for the right reason (`ImportError`, not
some unrelated error) before any implementation existed, at the specific
commit before implementation landed.

**What was wrong:**

- **SSH access broke mid-task, and the fix surfaced two independent
  wrong assumptions, not one.** Access to the shared server
  (10.110.11.31) stopped working outright (`Permission denied
  (publickey,password)`) partway through Step 2 of the job-queue work.
  The user provided the real login (username `satsandesh`, not
  `veerendra` — a wrong assumption carried from earlier in this
  engagement without ever being checked against the one place it was
  actually written down, `infra/deploy/sync-all.sh`, which had the
  correct username the whole time). Fixing the username alone wasn't
  enough: the first real test run then failed with `role "postgres"
  does not exist` — the actual Postgres superuser is `satsandesh`
  (`.env`'s `POSTGRES_USER`), not the placeholder `postgres` used in
  every earlier session's throwaway-DB script. Both were caught by
  reading the actual `.env`/script content directly rather than
  continuing to assume, per HONESTY — not by guessing again.
- **Direct uvicorn boot hit the exact `contracts/` import gap this
  journal already documented once, for the CI-job phase and pytest.**
  Standing up a real containerized gateway (not through pytest, for the
  Step 3 kill-and-restart proof and the Step 2 live proof) crashed with
  `ModuleNotFoundError: No module named 'contracts'` — the same root
  cause as the "Known issue, flagged Week 3 Phase 7" entry above
  (`services/gateway/conftest.py`'s `sys.path` trick is pytest-only, and
  does nothing for a real `uvicorn` process), just rediscovered the hard
  way instead of remembered. Fixed the same way that entry already
  settled on: `PYTHONPATH` set to the repo root on the container
  invocation, not a code change.
- **A retention test failed on `ObjectDeletedError`, and the bug was in
  the test, not `app/retention.py`.** `tests/conftest.py`'s `db_session`
  fixture expires ORM objects on commit by default. Three of six new
  retention tests accessed `.id` on an already-swept `MediaObject`
  instance *after* the sweep (which runs in, and commits via, an
  entirely separate `Session`) had already deleted that row for real —
  triggering a lazy reload against a row that no longer existed, instead
  of the assertion actually running. Confirmed on the server (all three
  failed with the same traceback), fixed by capturing each id into a
  plain variable immediately after creation, before any commit — not by
  changing `app/retention.py`, which was correct throughout.
- **`ruff check`/`ruff format` were not installed locally** for most of
  this session, so every formatting nit round-tripped through a full
  server test run to discover. Installed `ruff` locally
  (`pip install ruff`, invoked as `python -m ruff` since the console
  script wasn't on `PATH`) partway through, which cut the
  fix-commit-rerun cycle from several minutes to a few seconds per
  issue — should have been done at the very start of the job-queue work,
  not partway through the retention work.
- **`test_config.py::test_settings_load_from_env` fails on every server
  run, unrelated to any of this week's code.** `docker compose run`
  inherits `docker-compose.yml`'s own `environment:` block for the
  `gateway` service, which sets `CORS_ORIGINS` from the server's real
  `ALLOWED_ORIGINS` (`.env`) — so the test's expectation of an empty
  default never holds under this specific test-invocation method, even
  though it holds fine under the real CI (`.github/workflows/ci.yml`,
  which runs bare `pytest` with no `docker-compose.yml` involved at
  all). Confirmed by running the identical test against `team/main`
  through the identical harness before concluding this wasn't a
  regression — it fails there too, for the same reason.

**Decided differently:** the job-queue and retention work continued on
the same branch (`feat/m2-week6-job-queue`) rather than starting a new
`feat/m2-week6-<thing>` branch per DISCIPLINE's usual one-branch-per-
phase rule. Reason: the retention sweeper's `transcribe_media` handler
needs `app/jobs.py`, which only exists on this branch — team/main won't
have it until the job-queue PR (#71) merges. Stacking a second branch on
an unmerged one is exactly the setup that caused this week's earlier,
separate incident (PR #63's code silently stranding on an orphaned
branch when its base PR merged and GitHub didn't retarget it) — avoiding
a second stacked branch and continuing linearly on the one already-open
PR was judged the safer of two imperfect options, not a default the
DISCIPLINE instruction actually anticipated. Flagged here rather than
silently deviating from the stated rule.
