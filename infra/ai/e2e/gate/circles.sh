#!/usr/bin/env bash
set -uo pipefail
cd ~/wt-gate || exit 1
DC="docker compose -p gate -f docker-compose.yml -f docker-compose.ai.yml"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2); PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
echo "=== circles and announcements, through Caddy, on the Postgres backbone"
docker run --rm --network gate_default -v ~/gate-state:/state -v ~/gate-walk:/walk gate-gateway python /walk/walk.py circles
echo "=== the same, as rows in Postgres (the gate stack's own database)"
psql_q "SELECT 'circle '||name||' kind='||kind FROM circles ORDER BY created_at"
psql_q "SELECT 'membership role='||m.role||' user='||u.name FROM memberships m JOIN users u ON u.id=m.user_id ORDER BY m.joined_at NULLS LAST, u.name" 2>&1 | head -6
psql_q "SELECT 'message in circle: status='||status||' kind='||kind||' pipeline='||coalesce(pipeline_state,'NULL') FROM messages WHERE target_type='circle'"
psql_q "SELECT 'alembic_version='||version_num FROM alembic_version"
