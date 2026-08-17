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
