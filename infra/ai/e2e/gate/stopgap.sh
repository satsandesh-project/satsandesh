#!/usr/bin/env bash
# usage: gate_stopgap.sh on|off   -- recreate ONLY the gate gateway with the stopgap on or off
set -uo pipefail
cd ~/wt-gate || exit 1
export POSTGRES_HOST_PORT=55435 CADDY_HOST_PORT=18500 PIPELINE_ENABLED=true
if [ "$1" = on ]; then export AI_ACCEPT_WEBM_AS_OGG_OPUS=true; else unset AI_ACCEPT_WEBM_AS_OGG_OPUS; fi
DC="docker compose -p gate -f docker-compose.yml -f docker-compose.ai.yml"
$DC up -d --no-deps --force-recreate gateway 2>&1 | tail -2
for _ in $(seq 1 40); do st=$(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q gateway)" 2>/dev/null); [ "$st" = healthy ] && break; sleep 3; done
echo "gateway: $st"
$DC exec -T gateway python -c "from app.config import get_settings as g; s=g(); print({k: getattr(s,k) for k in ('PIPELINE_ENABLED','AI_ACCEPT_WEBM_AS_OGG_OPUS','UNDO_WINDOW_SECONDS','AI_TRANSCRIBE_URL')})" 2>&1 | tail -1
docker ps --format '{{.Names}} {{.Status}}' | grep '^gate-' | sed 's/^/  /'
