#!/usr/bin/env bash
# Push the current work to every place it needs to live:
#   1. team repo       (team)    -- as a BRANCH ONLY, never main
#   2. local storage             -- already the working copy, nothing to do
#   3. server storage            -- git pull over SSH
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

# 1. Team repo -- branch only. Refuse outright if someone runs this on main,
#    rather than trusting the remote's branch protection to catch it.
echo
echo "==> [1/3] team repo (team)"
if [ "$BRANCH" = "main" ]; then
  echo "    SKIPPED: refusing to push 'main' to the team repo." >&2
  echo "    Work on a feature branch (e.g. feat/m1-<what>) and open a PR." >&2
else
  git push team "$BRANCH"
  echo "    Open/update the PR:"
  echo "    https://github.com/satsandesh-project/satsandesh/pull/new/$BRANCH"
fi

# 2. Local storage is the working copy this script is running in.
echo
echo "==> [2/3] local storage: already current (this working copy)"

# 3. Server. Pulls from the TEAM repo (public, read-only pull needs no
#    credentials). The server's clone also has a stale "origin" remote
#    pointing at the personal fork -- do not pull from it.
#
#    $SERVER_PATH's working tree also backs the live deployment
#    (docker compose up builds from exactly this checkout) -- confirmed
#    2026-09-23 while debugging a failed sync: it sits on `main`, not
#    whatever feature branch is being synced, and that's deliberate, not
#    an oversight. A plain `git pull team $BRANCH` while main is checked
#    out tries to MERGE an unmerged, unreviewed feature branch straight
#    into that main working tree -- at best it fails outright (no git
#    identity configured on this shared account, confirmed the same day),
#    at worst it would silently succeed, leaving $SERVER_PATH's `main`
#    containing commits that were never actually merged on GitHub and one
#    `docker compose up --build` away from deploying them for real.
#
#    So: syncing `main` itself really does pull (fast-forward only -- if
#    that's not possible, something is wrong and this should fail loudly,
#    not silently create a merge commit). Syncing anything else only
#    fetches -- the branch's commits land on the server as `team/$BRANCH`,
#    available to `git worktree add` from (the pattern every test run this
#    week actually used), without ever touching the checked-out working
#    tree the live deployment depends on.
echo
echo "==> [3/3] server ($SERVER)"
if [ "$SKIP_SERVER" = "1" ]; then
  echo "    SKIPPED (--skip-server)"
elif [ "$BRANCH" = "main" ]; then
  ssh "$SERVER" "cd $SERVER_PATH && git pull --ff-only team main && git rev-parse HEAD"
  echo
  echo "    Remember: a code change needs a rebuild, not just a pull --"
  echo "      docker compose build <service>"
  echo "      docker compose up -d <service>"
  echo "    and a Caddyfile change needs: docker compose restart caddy"
  echo "    (both learned the hard way -- see docs/prompt-journal.md)"
else
  ssh "$SERVER" "cd $SERVER_PATH && git fetch team $BRANCH && git rev-parse team/$BRANCH"
  echo
  echo "    Server's checked-out working tree is untouched (still whatever"
  echo "    it was before -- normally main, which the live deployment"
  echo "    builds from). Only the team/$BRANCH ref was updated. Use"
  echo "    'git worktree add' there to test this branch's actual code."
fi

echo
echo "==> done. $HEAD_SHA is now on: $([ "$BRANCH" != "main" ] && echo "team ($BRANCH), ")local$([ "$SKIP_SERVER" = "0" ] && echo ", server")"
