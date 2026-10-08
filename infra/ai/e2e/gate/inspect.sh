#!/usr/bin/env bash
# usage: gate_inspect.sh <message_id> [wait_seconds]   -- what the gate stack did with one message
set -uo pipefail
MID="$1"; WAIT="${2:-0}"
cd ~/wt-gate || exit 1
DC="docker compose -p gate -f docker-compose.yml -f docker-compose.ai.yml"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2); PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
MODTOK=$($DC exec -T gateway python -m app.tokens 00000000-0000-4000-8000-0000000000a1 | tr -d '\r\n')  # /moderation/* needs a signed token
run() { docker run --rm -e MODERATOR_TOKEN="$MODTOK" --network gate_default -v ~/gate-state:/state -v ~/gate-walk:/walk gate-gateway python /walk/walk.py "$@"; }
[ "$WAIT" -gt 0 ] && sleep "$WAIT"
echo "=== database: the message, its job, its moderation trail ($(date -u +%T)Z)"
psql_q "SELECT 'message status='||status||' pipeline_state='||coalesce(pipeline_state,'NULL')||' kind='||kind||' transcript_set='||(transcript IS NOT NULL)||' pivot_set='||(pivot_text_en IS NOT NULL) FROM messages WHERE id='$MID'"
psql_q "SELECT 'job '||status||' attempts='||attempts||'/'||max_attempts||' last_error='||left(coalesce(last_error,''),170) FROM jobs WHERE payload->>'message_id'='$MID'"
psql_q "SELECT 'event '||actor_kind||' '||action||' label='||label||' degraded='||degraded||' | '||left(rationale,150) FROM moderation_events WHERE message_id='$MID' ORDER BY created_at"
psql_q "SELECT 'renderings '||coalesce(string_agg(language||':'||coalesce(degraded_reason,'ok'), ', '),'none') FROM message_renderings WHERE message_id='$MID'"
echo "=== what the HINDI RECEIVER sees"; run inspect "$MID" receiver
echo "=== what the TELUGU ELDER (author) sees"; run inspect "$MID" elder
echo "=== moderator's view (direct to the gateway)"; run moderation "$MID"
