#!/usr/bin/env bash
# Push the current work to every place it needs to live:
#   1. personal repo   (origin)  -- direct to the current branch
#   2. team repo       (team)    -- as a BRANCH ONLY, never main
#   3. local storage             -- already the working copy, nothing to do
#   4. server storage            -- git pull over SSH
#
# Usage, from the repo root:
#   ./infra/deploy/sync-all.sh                    # push current branch everywhere
#   ./infra/deploy/sync-all.sh --skip-server      # skip the SSH step
#
# Why the team repo is branch-only: satsandesh-project/satsandesh is the
# shared canonical repo with other members' merged work, and its history
# is UNRELATED to this repo's (no common ancestor -- `git merge-base`
# returns nothing). Pushing this repo's main onto team main would need
# --force and would erase everyone else's commits. Integration there
# happens through pull requests, the same way the other members work.
set -euo pipefail

SERVER="satsandesh@10.110.11.31"
SERVER_PATH="~/veerendra"
SKIP_SERVER=0
[ "${1:-}" = "--skip-server" ] && SKIP_SERVER=1

BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHA=$(git rev-parse HEAD)

if [ -n "$(git status --porcelain)" ]; then
  echo "Uncommitted changes present -- commit them first, then re-run:" >&2
  git status --short >&2
  exit 1
fi

echo "==> branch: $BRANCH   HEAD: $HEAD_SHA"

# 1. Personal repo. This one is yours, so pushing the branch directly is fine.
echo
echo "==> [1/4] personal repo (origin)"
git push origin "$BRANCH"

# 2. Team repo -- branch only. Refuse outright if someone runs this on main,
#    rather than trusting the remote's branch protection to catch it.
echo
echo "==> [2/4] team repo (team)"
if [ "$BRANCH" = "main" ]; then
  echo "    SKIPPED: refusing to push 'main' to the team repo." >&2
  echo "    Work on a feature branch (e.g. feat/m1-<what>) and open a PR." >&2
else
  git push team "$BRANCH"
  echo "    Open/update the PR:"
  echo "    https://github.com/satsandesh-project/satsandesh/pull/new/$BRANCH"
fi

# 3. Local storage is the working copy this script is running in.
echo
echo "==> [3/4] local storage: already current (this working copy)"

# 4. Server. Pulls from the PERSONAL repo, which is what it's cloned from.
echo
echo "==> [4/4] server ($SERVER)"
if [ "$SKIP_SERVER" = "1" ]; then
  echo "    SKIPPED (--skip-server)"
else
  ssh "$SERVER" "cd $SERVER_PATH && git pull origin $BRANCH && git rev-parse HEAD"
  echo
  echo "    Remember: a code change needs a rebuild, not just a pull --"
  echo "      docker compose build <service>"
  echo "      docker compose up -d <service>"
  echo "    and a Caddyfile change needs: docker compose restart caddy"
  echo "    (both learned the hard way -- see docs/prompt-journal.md)"
fi

echo
echo "==> done. $HEAD_SHA is now on: origin, $([ "$BRANCH" != "main" ] && echo "team ($BRANCH), ")local$([ "$SKIP_SERVER" = "0" ] && echo ", server")"
