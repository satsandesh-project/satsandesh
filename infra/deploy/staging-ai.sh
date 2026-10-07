#!/usr/bin/env bash
# Puts the REAL AI services (ASR, MT, render, moderation) and the pipeline on a RUNNING compose
# stack (staging), layering docker-compose.ai.yml onto exactly the files that stack was started with.
#
#   infra/deploy/staging-ai.sh              plan: checks and the exact commands; changes NOTHING
#   infra/deploy/staging-ai.sh --apply      do it
#   infra/deploy/staging-ai.sh --rollback   gateway back to the mock AI and the pipeline off;
#                                           the AI containers are STOPPED, their volumes kept
#
#   STAGING_DIR  the stack's working dir      (default ~/kshitiz/live-app)
#   PROJECT      its compose project name     (default liveapp)
#   STOPGAP      1 = AI_ACCEPT_WEBM_AS_OGG_OPUS=true (default), 0 = leave it off
#
# What it changes, so nobody is surprised: the gateway is RECREATED (a few seconds of 5xx), four AI
# containers are added (about 6 GB of RAM and several GB of model downloads on the first start),
# the pipeline turns on, so every voice note and text message is now transcribed, translated,
# moderated and rendered before delivery (and the sender's note waits for that, at least the 30 s undo
# window), and, with STOPGAP=1, a browser's webm_opus is treated as ogg_opus. THAT STOPGAP IS NOT THE
# #2 DECISION: without it a real browser recording is HELD (OPEN_QUESTIONS #19); with it the flow
# works. The Telugu ASR is unreliable (OPEN_QUESTIONS #31): a wrong-script transcript is delivered
# with no signal. Know that before turning it on for people.
#
# Needs HF_TOKEN in the stack's .env (the IndicTrans2 models are gated). This script never prints it
# and never writes it: whoever owns the stack puts it there.
set -uo pipefail

MODE=plan
for arg in "$@"; do
  case "$arg" in
    --apply) MODE=apply ;;
    --rollback) MODE=rollback ;;
    -h | --help) sed -n 2,26p "$0"; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

STAGING_DIR="${STAGING_DIR:-$HOME/kshitiz/live-app}"
PROJECT="${PROJECT:-liveapp}"
STOPGAP="${STOPGAP:-1}"
die() { echo "ERROR: $*" >&2; exit 1; }
cd "$STAGING_DIR" 2>/dev/null || die "no such directory: $STAGING_DIR"

# The files the RUNNING gateway was started with: layer onto exactly that, so nothing about the
# stack (the TLS override, the ports) silently reverts.
GW=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" \
  --filter "label=com.docker.compose.service=gateway" | head -1)
BASE=()
if [ -n "$GW" ]; then
  LABEL=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$GW")
  IFS=, read -ra FILES <<<"$LABEL"
  for f in "${FILES[@]}"; do BASE+=(-f "$f"); done
else
  echo "WARNING: no running gateway for project '$PROJECT'; assuming just docker-compose.yml" >&2
  BASE=(-f docker-compose.yml)
fi
AI_FILE="$STAGING_DIR/docker-compose.ai.yml"
DC_BASE=(docker compose -p "$PROJECT" "${BASE[@]}")
DC_AI=(docker compose -p "$PROJECT" "${BASE[@]}" -f "$AI_FILE")

settings() {  # the gateway's EFFECTIVE settings, from inside the container
  "${DC_BASE[@]}" exec -T gateway python -c "from app.config import get_settings as g; s=g(); print({k: getattr(s,k) for k in ('PIPELINE_ENABLED','AI_ACCEPT_WEBM_AS_OGG_OPUS','UNDO_WINDOW_SECONDS','AUTH_MODE','AI_TRANSCRIBE_URL','AI_PIVOT_URL')})" 2>&1 | tail -1
}
wait_healthy() {  # $1.. = services; up to 25 minutes (the first start downloads the models)
  for svc in "$@"; do
    for _ in $(seq 1 300); do
      cid=$("${DC_AI[@]}" ps -q "$svc" 2>/dev/null)
      [ -n "$cid" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)" = healthy ] && break
      sleep 5
    done
    echo "  $svc: $(docker inspect -f '{{.State.Health.Status}}' "$("${DC_AI[@]}" ps -q "$svc")" 2>/dev/null)"
  done
}

FAILED=0
check() {  # $1 = description, $2 = 0 for ok
  if [ "$2" = 0 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; FAILED=1; fi
}

echo "stack: project=$PROJECT dir=$STAGING_DIR commit=$(git rev-parse --short HEAD 2>/dev/null)"
echo "files it runs with: ${BASE[*]}"
echo "gateway now: $( [ -n "$GW" ] && settings || echo 'not running')"

if [ "$MODE" = rollback ]; then
  echo "=== rollback: the gateway goes back to the mock AI and the pipeline off; AI containers stopped"
  env -u PIPELINE_ENABLED -u AI_ACCEPT_WEBM_AS_OGG_OPUS "${DC_BASE[@]}" up -d --no-deps --force-recreate gateway
  "${DC_AI[@]}" stop speech moderation mt render
  echo "gateway now: $(settings)"
  exit 0
fi

echo "=== checks"
[ -f "$AI_FILE" ]; check "docker-compose.ai.yml exists at this commit" $?
[ "$(grep -c '^HF_TOKEN=.' .env 2>/dev/null)" -ge 1 ]; check "HF_TOKEN is set in .env (value never shown)" $?
free_gb=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc 0-9)
[ "${free_gb:-0}" -ge 25 ]; check "free disk >= 25 GB (have ${free_gb:-?} GB)" $?
mem_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
[ "${mem_mb:-0}" -ge 8000 ]; check "free memory >= 8000 MiB (have ${mem_mb:-?} MiB)" $?
[ -n "$GW" ]; check "the stack's gateway is running" $?

APPLY_ENV="PIPELINE_ENABLED=true"
[ "$STOPGAP" = 1 ] && APPLY_ENV="$APPLY_ENV AI_ACCEPT_WEBM_AS_OGG_OPUS=true"
echo "=== what --apply runs"
echo "  1. ${DC_AI[*]} up -d --build speech moderation mt render"
echo "  2. $APPLY_ENV ${DC_AI[*]} up -d --no-deps --force-recreate gateway"
echo "  3. checks the gateway's effective settings and the AI services' health"
echo "  rollback: $0 --rollback   (same STAGING_DIR / PROJECT)"

[ "$MODE" = plan ] && { [ "$FAILED" = 0 ] && echo "plan OK" || echo "plan: FIX THE FAILED CHECKS FIRST"; exit "$FAILED"; }
[ "$FAILED" = 0 ] || die "checks failed; nothing was changed"

echo "=== 1. the AI services (first start downloads the models: be patient)"
"${DC_AI[@]}" up -d --build speech moderation mt render || die "could not start the AI services"
wait_healthy moderation speech mt render
echo "=== 2. the gateway, recreated with the pipeline on"
env PIPELINE_ENABLED=true $([ "$STOPGAP" = 1 ] && echo AI_ACCEPT_WEBM_AS_OGG_OPUS=true) \
  "${DC_AI[@]}" up -d --no-deps --force-recreate gateway || die "could not recreate the gateway"
for _ in $(seq 1 40); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$("${DC_AI[@]}" ps -q gateway)" 2>/dev/null)" = healthy ] && break
  sleep 3
done
echo "=== 3. verify"
echo "gateway now: $(settings)"
for svc in gateway speech moderation mt render; do
  echo "  $svc: $(docker inspect -f '{{.State.Health.Status}}' "$("${DC_AI[@]}" ps -q "$svc")" 2>/dev/null)"
done
echo "done. Send a Telugu voice note from the app and watch it: 'docker compose -p $PROJECT logs -f gateway'."
echo "To undo: $0 --rollback"
