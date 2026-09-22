# Proposal: wire services/ai/'s tests into CI

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
