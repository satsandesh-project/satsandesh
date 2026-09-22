# Proposal: wire services/ai/'s tests into CI

## SUPERSEDED — 2026-09-22

**This proposal is superseded. `main` already has a working fix.** Two new
jobs, `gateway-tests` and `ai-tests`, were added to `.github/workflows/ci.yml`
by Sainathan/M4 (see that file's own header comments, and
`docs/journal/sainathan.md`, 2026-09-22) — separately from, and slightly
differently than, what this proposal spelled out below. `lint-and-test` (the
original job, running bare `pytest` from the repo root against
`tests/test_placeholder.py`) is untouched.

**One thing this proposal didn't anticipate:** it explicitly recommended
*against* `pip install -e services/ai[dev]`, reasoning that
`services/ai/pyproject.toml` "has no `[build-system]` table and its venv has
no `setuptools` installed." The shipped `ai-tests` job does exactly that
(`pip install -e ".[dev]"` from `services/ai/`) — and at some point between
this proposal and today that command genuinely failed with `Multiple
top-level packages discovered in a flat-layout` (setuptools' auto-discovery
tripping over `services/ai/` having more than one top-level package —
`mock/`, `moderation/`, `speech/`). That got fixed properly, in
`services/ai/pyproject.toml` itself (commit `3567e09`, `fix(ai): keep pip
install -e .[dev] working with more than one package in services/ai`, adding
an explicit `[tool.setuptools]\npackages = []`), not by avoiding the editable
install as this proposal suggested.

**Re-verified today, standalone**, on `feat/ai-speech-asr-w5`'s current
state (merged up to date with `main`):

```
services\ai\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```
succeeds cleanly — no "Multiple top-level packages discovered" error, no
other error. Then, bare `pytest` from inside `services/ai/`, exactly as the
`ai-tests` job runs it:

```
services\ai\.venv\Scripts\python.exe -m pytest
111 passed, 1 warning in 1.94s
```

So the `ai-tests` job as it exists on `main` today would pass.

**The one real remaining action item — not this lane's to fix:** per
`.github/workflows/ci.yml`'s own trailing comment, branch protection on
`main` currently only requires `lint-and-test`. `gateway-tests` and
`ai-tests` are not yet added to the required status checks, so a red run in
either currently cannot block a merge. That's a repo-admin
(`satsandesh-project`) setting, not something owned by whoever wrote this
proposal or works in `services/ai/`.

The original proposal is kept below, unedited, as the historical record of
what the gap looked like before the fix landed.

---

**Date:** 2026-09-19
**Status:** proposal only — not applied. Both files this touches are outside
`services/ai/` and `docs/`, which is this phase's scope, so this document exists
to hand off to whoever owns `.github/workflows/ci.yml` as its own small PR.
**Confirms the finding from `docs/AI_LANE_INVENTORY.md` §4**, with the concrete
fix spelled out.

## The gap, reconfirmed today

CI's "Run tests" step runs bare `pytest` from the repo root. The root
`pyproject.toml` sets `testpaths = ["tests"]`, so that command collects exactly
one test:

```
tests/test_placeholder.py::test_placeholder
1 test collected
```

All 66 tests under `services/ai/tests/` — contract validation, golden-fixture
round-trips, and mock-server behavior — never run in CI. Re-verified today,
standalone, using `services\ai\.venv\Scripts\python.exe`:

```
66 passed, 1 warning in 1.10s
```

Lint is unaffected by this gap — `ruff check` and `ruff format --check` already
run over the whole repo (`.`) in CI, so `services/ai/` and `contracts/ai/` are
already covered there. Confirmed clean today:

```
ruff check services/ai contracts/ai        -> All checks passed!
ruff format --check services/ai contracts/ai -> 28 files already formatted
```

So the only missing piece is test *collection*, not lint, and not (as far as
this lane is concerned) anything about `services/ai/`'s own configuration —
`services/ai/pyproject.toml` already sets `testpaths = ["tests"]` correctly for
standalone use from within that directory.

## The minimal fix

An explicit path argument to `pytest` overrides `testpaths` from config, so this
does **not** require touching the root `pyproject.toml` at all — only
`.github/workflows/ci.yml`, adding one dependency-install step and one test-run
step scoped to this lane:

```diff
       - name: Run tests
         run: pytest
+
+      - name: Install AI-lane test dependencies
+        run: pip install pydantic fastapi uvicorn pytest httpx
+
+      - name: Run services/ai tests
+        run: pytest services/ai/tests -v
```

Notes on that patch:

- Dependency versions deliberately aren't pinned here, mirroring the existing
  `pip install ruff pytest` step's style; if the team wants pinned versions,
  pull them from `services/ai/pyproject.toml`'s `dependencies` /
  `optional-dependencies.dev` lists (`pydantic>=2.6`, `fastapi>=0.110`,
  `uvicorn>=0.29`, `pytest>=8.0`, `httpx>=0.27`) rather than `-e services/ai[dev]`
  — that package has no `[build-system]` table and its venv has no `setuptools`
  installed, so an editable install would pull in an isolated build step that
  isn't needed just to run the tests.
- `services/ai/conftest.py` already inserts the repo root onto `sys.path`, so
  `contracts.ai.*` imports resolve correctly no matter which directory `pytest`
  is invoked from — no `PYTHONPATH` wrangling needed in the workflow.
- This only adds a step; it does not change or remove the existing bare
  `pytest` step, so `tests/test_placeholder.py` keeps running exactly as before.

## Who applies this

`.github/CODEOWNERS` lists `/.github/workflows/` as CONTESTED
(`@sainathanv @kpspyolo024`) — see `docs/AI_LANE_INVENTORY.md` §5. This PR
should go through one of them, as its own small change, separate from any
`services/ai/` work.
