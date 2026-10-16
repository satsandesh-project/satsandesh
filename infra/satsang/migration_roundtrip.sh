#!/usr/bin/env bash
# Proves the satsang migrations can be applied AND reversed on a REAL Postgres: a throwaway database
# on the shared server's own Postgres (veerendra-postgres-1), a throwaway worktree, never staging.
#
#   bash infra/satsang/migration_roundtrip.sh [ref-on-team] [revision staging is at]
#   e.g. bash infra/satsang/migration_roundtrip.sh feat/m2-w10-reminder-delivery a7c2e91f4b36
#
# Run from ~/veerendra on the server (needs its .env for the Postgres credentials, which are never
# printed). Steps: upgrade a fresh database to the revision staging is at; take a schema dump; upgrade
# to head; put real rows in the new tables; downgrade one step at a time WITH those rows present; check
# the schema equals the first dump; upgrade again; downgrade in one command and check again.
# `pg_dump` writes a random "\restrict" token into every dump, so those two lines are ignored in the
# comparison; nothing else is.
set -uo pipefail
REF="${1:-main}"; FROM="${2:-a7c2e91f4b36}"
cd ~/veerendra || exit 1
PG_USER=$(grep -E "^POSTGRES_USER=" .env | cut -d= -f2); PG_PASS=$(grep -E "^POSTGRES_PASSWORD=" .env | cut -d= -f2)
git fetch -q team
WT=~/tmp-roundtrip-$$; DB="throwaway_rt_$$"; PGC=veerendra-postgres-1
git worktree add -q --detach "$WT" "team/$REF" || exit 1
echo "ref under test: team/$REF at $(git -C "$WT" rev-parse --short HEAD); staging is at $FROM"
docker exec $PGC psql -q -U "$PG_USER" -c "CREATE DATABASE $DB;" >/dev/null
cleanup() {
  docker exec $PGC psql -q -U "$PG_USER" -c "DROP DATABASE IF EXISTS $DB;" >/dev/null 2>&1
  git -C ~/veerendra worktree remove --force "$WT" 2>/dev/null; rm -f /tmp/rt_*.sql
  echo "cleanup done (throwaway database dropped, worktree removed)"
}
trap cleanup EXIT
alembic() {
  docker compose run --rm --no-deps -T -v "$WT":/workspace -w /workspace/services/gateway \
    -e DATABASE_URL="postgresql://$PG_USER:$PG_PASS@postgres:5432/$DB" -e JWT_SECRET=ci-placeholder-secret \
    -e VAPID_PRIVATE_KEY=x -e VAPID_PUBLIC_KEY=x -e VAPID_SUBJECT=mailto:ci@example.com \
    -e MEDIA_STORAGE_ROOT=/tmp/m -e CORS_ORIGINS= \
    gateway bash -c "pip install -q -r requirements.txt >/dev/null 2>&1 && python -m alembic $*" </dev/null 2>&1 \
    | grep -v -i -E "WARNING|pip|notice"
}
sql() { printf '%s\n' "$1" | docker exec -i $PGC psql -U "$PG_USER" -d "$DB" -t -A 2>&1; }
dump() { docker exec $PGC pg_dump -s -U "$PG_USER" "$DB" | grep -v -E '^\\(un)?restrict ' > "$1"; echo "schema dump $1: $(wc -l < "$1") lines"; }
same() { if diff -q "$1" "$2" >/dev/null; then echo "$3: IDENTICAL"; else echo "$3: DIFFERENT"; diff "$1" "$2" | head -20; fi; }
exists() { echo "  sessions: $(sql "select to_regclass('public.sessions') is not null")  renderings: $(sql "select to_regclass('public.satsang_reminder_renderings') is not null")  trigger fn: $(sql "select count(*) from pg_proc where proname='sessions_check_status_transition'")"; }

echo; echo "## 0. staging's state: a fresh database upgraded to $FROM"
alembic upgrade "$FROM" | tail -1; alembic current; exists; dump /tmp/rt_A.sql
echo "alembic heads (must be exactly one):"; alembic heads
echo; echo "## 1. alembic upgrade head"; alembic upgrade head; alembic current; exists; dump /tmp/rt_C.sql
echo; echo "## 2. real rows in the new tables"
U=$(sql "insert into users (id, name, preferred_language, role) values (gen_random_uuid(), 'rt test', 'te', 'admin') returning id" | head -1)
S=$(sql "insert into sessions (id, title, speaker_user_id, scheduled_start_at, stream_key_sha256, created_by) values (gen_random_uuid(), 'roundtrip', '$U', now() + interval '2 hours', repeat('a',64), '$U') returning id" | head -1)
sql "insert into satsang_reminder_renderings (session_id, language, text, translated) values ('$S', 'te', 'x', true) returning 1" | head -1
sql "insert into jobs (id, job_type, payload, status) values (gen_random_uuid(), 'satsang_reminder', '{\"session_id\": \"$S\"}', 'queued') returning 1" | head -1
echo "rows now: sessions=$(sql 'select count(*) from sessions') renderings=$(sql 'select count(*) from satsang_reminder_renderings') queued reminder jobs=$(sql "select count(*) from jobs where job_type='satsang_reminder'")"
echo; echo "## 3. downgrade -1, then -1 again, WITH THE ROWS PRESENT"
alembic downgrade -1; alembic current; exists
alembic downgrade -1; alembic current; exists; dump /tmp/rt_B.sql
same /tmp/rt_A.sql /tmp/rt_B.sql "schema after the two downgrades vs staging's state"
echo "NOT removed by a downgrade: queued satsang_reminder jobs = $(sql "select count(*) from jobs where job_type='satsang_reminder'") (the old code has no handler for them); users rows = $(sql 'select count(*) from users')"
echo; echo "## 4. forward again"; alembic upgrade head | tail -2; alembic current; exists; dump /tmp/rt_D.sql
same /tmp/rt_C.sql /tmp/rt_D.sql "schema after re-upgrade vs after the first upgrade"
echo; echo "## 5. one command: alembic downgrade $FROM"; alembic downgrade "$FROM"; alembic current; exists; dump /tmp/rt_E.sql
same /tmp/rt_A.sql /tmp/rt_E.sql "schema after the one-shot downgrade vs staging's state"
