# Prompt Journal — Student 2 (Platform & backbone)

Team practice: log the key prompts behind AI-assisted work, plus notable
corrections, so a human reviewer can trace how code was produced. See
`README.md` → Process.

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
