#!/usr/bin/env bash
# Brings up the GATE stack: a staging-equivalent built from team/main, own compose project `gate`,
# own volumes, own ports. NOT staging (liveapp, M1's folder) and it touches nothing of it.
# Real: gateway, ASR (faster-whisper small), MT, render (+Piper), Caddy with the real Caddyfile.
# Stub: moderation (keyword). The elder-app is NOT built here (its source is read, not run).
# Stopgap AI_ACCEPT_WEBM_AS_OGG_OPUS is deliberately NOT set: it is turned on later, on purpose.
set -uo pipefail
cd ~/veerendra || exit 1
git fetch -q team
WT=~/wt-gate
[ -d "$WT" ] || git worktree add -q --detach "$WT" team/main || exit 1
cp ~/veerendra/.env "$WT/.env"
cd "$WT" || exit 1
echo "GATE_HEAD=$(git rev-parse --short HEAD) ($(git log -1 --format=%s | cut -c1-70))"
export POSTGRES_HOST_PORT=55435 CADDY_HOST_PORT=18500 PIPELINE_ENABLED=true
DC="docker compose -p gate -f docker-compose.yml -f docker-compose.ai.yml"
$DC up -d --build postgres gateway speech moderation mt render 2>&1 | tail -6
$DC up -d --no-deps caddy 2>&1 | tail -3
for svc in postgres moderation gateway speech mt render; do
  for _ in $(seq 1 150); do
    cid=$($DC ps -q "$svc"); st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)
    [ "$st" = healthy ] && break; sleep 5
  done
  echo "$svc: $(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q "$svc")" 2>/dev/null)"
done
echo "effective gateway settings:"
$DC exec -T gateway python -c "from app.config import get_settings as g; s=g(); print({k: getattr(s,k) for k in ('PIPELINE_ENABLED','AUTH_MODE','AI_ACCEPT_WEBM_AS_OGG_OPUS','UNDO_WINDOW_SECONDS','AI_TRANSCRIBE_URL','AI_PIVOT_URL','AI_RENDER_URL','AI_MODERATION_URL')})" 2>&1 | tail -2
echo GATE_UP_DONE
