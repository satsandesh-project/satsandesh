# Contributing Conventions — SatSandesh

Working agreement for all four members. Applies to everyone, every week — no exceptions,
including for small changes.

## Branching

- Branch protection isn't yet enabled (private repo, requires a GitHub paid tier). Until it is,
  this is an honor-system rule: **do not push directly to main**, even though GitHub currently
  lets you. Violating this quietly undoes the entire PR-review discipline the project depends on.
- Branch names: `<type>/<short-description>`, e.g. `feat/gateway-auth`, `fix/ws-reconnect`,
  `chore/ci-workflow`, `docs/readme-update`.
- Types: `feat`, `fix`, `chore`, `docs`, `refactor`.
- Always branch from an up-to-date `main`: `git checkout main && git pull` before creating a
  new branch.

## Commits

- Small, single-concern commits. A commit does one thing.
- Config/tooling changes commit separately from feature code.
- Commit messages: short imperative summary, e.g. `fix: correct target_id mapping in GET /messages`.

## Pull requests

- Every change goes through a PR, even small ones and even from the person who wrote this file.
- PR description says what it does and why — not "updates" or "changes."
- CI must be green before merge.
- At least one other member reviews before merge.
- **Nothing merges unread.** The author explains their PR — what it does, why, anything
  non-obvious — either in the PR description or out loud in review. This is the core discipline
  of the project, not a formality.
- Reviewer actually reads the diff. A review isn't a rubber stamp; if something's unclear, ask
  before approving.
- Delete the branch after merge (GitHub can do this automatically on merge).

## Review rotation

Pairs rotate weekly per the Month 1 schedule. Within a given piece of work, whoever isn't paired
with the author reviews it. The goal: every PR gets read by at least one person who wasn't in the
room when it was written.

## Scope

- Stay inside your own service folder unless a task explicitly says otherwise.
- Touching a teammate's folder or a shared file (`contracts/`, root config) — flag it to them
  first, even if it's a small fix.
- Never touch `.git/` directly, and never `git reset --hard`, `git clean -fdx`, or force-push to
  a shared branch.

## Before every PR

- Tests pass locally.
- `ruff check .` and `ruff format --check .` pass locally (or let pre-commit catch it — see
  `.pre-commit-config.yaml`).
- A prompt journal entry added under `docs/journal/<your-name>.md` for anything notable.

## When CI fails

- Read the actual log, not just the red X. Click into the failing step.
- Fix and push a new commit to the same branch — don't open a second PR for the same work.
- If genuinely stuck after a real attempt, ask the team rather than force-merging or disabling
  the check.
