#!/usr/bin/env bash
# PROVES "graceful degradation" (services/gateway/ORCHESTRATOR_DESIGN.md section 4 and 7, the Week 8
# M2 deliverable) on a REAL stack: the real gateway, speech, mt, moderation and render, and a real
# Postgres. Until this ran, the behaviour was shown only against a fake AI service.
#
# One throwaway compose project, `degrade`, built from team/main. It is NEVER staging (liveapp) and
# touches nothing of it. Each case stops (or breaks) one service, sends one message, watches the
# pipeline to its final state, reads the result from the database AND from what the READER's own
# GET /messages returns, prints a PASS/FAIL verdict, and restores the service.
#
#   usage:  scp degrade_probe.py to /tmp/ on the server, then:  bash degrade_proof.sh < /dev/null
#   (stdin must be closed: `docker compose exec` otherwise reads the rest of this script from it)
#
# JOB_MAX_ATTEMPTS=5 (the default) so "after the retries are used up" takes ~76 s (backoff 5, 10, 20,
# 40 s) and the render-comes-back case has room to succeed. Exits 1 if any case fails.
set -uo pipefail
cd ~/veerendra && git fetch -q team && rm -rf ~/wt-degrade 2>/dev/null; git worktree prune
git worktree add -q --detach ~/wt-degrade team/main || exit 1
cp ~/veerendra/.env ~/wt-degrade/.env; cd ~/wt-degrade || exit 1
echo "stack built from team/main $(git rev-parse --short HEAD)"
[ -s /tmp/degrade_probe.py ] || { echo "/tmp/degrade_probe.py is missing: copy it first"; exit 1; }
export POSTGRES_HOST_PORT=55451 CADDY_HOST_PORT=18570
export PIPELINE_ENABLED=true UNDO_WINDOW_SECONDS=0 JOB_MAX_ATTEMPTS=5 AI_ACCEPT_WEBM_AS_OGG_OPUS=false
DC="docker compose -p degrade -f docker-compose.yml -f docker-compose.ai.yml"
$DC up -d --build postgres gateway speech moderation mt render </dev/null > /tmp/degrade_build.log 2>&1
tail -2 /tmp/degrade_build.log | cut -c1-120

healthy() {
  for s in "$@"; do
    for _ in $(seq 1 240); do
      [ "$(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q "$s" </dev/null)" 2>/dev/null)" = healthy ] && break
      sleep 4
    done
    echo "  $s: $(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q "$s" </dev/null)" 2>/dev/null)"
  done
}
healthy postgres gateway moderation mt render speech
P() { docker exec -i "$($DC ps -q gateway </dev/null)" python - "$@" < /tmp/degrade_probe.py; }
$DC exec -T gateway python -c "from app.config import get_settings as g; s=g(); print('  settings:', {k: getattr(s,k) for k in ('PIPELINE_ENABLED','JOB_MAX_ATTEMPTS','UNDO_WINDOW_SECONDS','AI_RENDER_AUDIO_ROOT')})" </dev/null 2>&1 | tail -1
P seed

FAILS=0; N=0
run_case() {  # run_case "<title>" "<service to stop or empty>" "<text|voice>" "<expect key>" "<what section 4/7 says>"
  local title="$1" stop="$2" kind="$3" expect="$4" says="$5" mid out
  N=$((N + 1)); echo; echo "################ CASE $N: $title"; echo "  DESIGN SAYS: $says"
  [ -n "$stop" ] && { $DC stop "$stop" </dev/null >/dev/null 2>&1; echo "  stopped: $stop"; }
  mid=$(P "send_$kind" | tail -1 | tr -d '\r'); echo "  message id: $mid"
  if [ -z "$mid" ]; then echo "  VERDICT FAIL: no message was sent"; FAILS=$((FAILS + 1)); return; fi
  P watch "$mid" 150; P inspect "$mid"
  out=$(P check "$mid" "$expect"); echo "$out"; echo "$out" | grep -q "VERDICT PASS" || FAILS=$((FAILS + 1))
  [ -n "$stop" ] && { $DC start "$stop" </dev/null >/dev/null 2>&1; healthy "$stop"; }
}

run_case "everything up, a Telugu text message to a Hindi reader" "" text delivered_full \
  "nothing is wrong: delivered with its rendering"
run_case "MODERATION is down (the gate itself)" moderation text held \
  "we cannot rule on it, so it is HELD for a human with a SYSTEM event; never delivered unclassified"
run_case "MT is down (no English pivot to rule on)" mt text held \
  "same: HELD for a human with a SYSTEM event"
run_case "SPEECH is down (a voice note cannot be transcribed)" speech voice held \
  "same: HELD for a human with a SYSTEM event"
run_case "RENDER is down, after moderation said ALLOW" render text delivered_bare \
  "translations are an enhancement and moderation is the gate: DELIVERED with whatever renderings exist"

echo; echo "################ CASE $((N + 1)): RENDER goes down and comes BACK while the job is still retrying"
N=$((N + 1)); echo "  DESIGN SAYS: the message waits behind its retrying job and is then delivered WITH its rendering; nothing is lost"
$DC stop render </dev/null >/dev/null 2>&1; echo "  stopped: render"
mid=$(P send_text | tail -1 | tr -d '\r'); echo "  message id: $mid"
sleep 8
echo "  --- while render is still down it must NOT have been delivered:"
P inspect "$mid" | sed -n '1p;/job:/p;/THE READER/p'
$DC start render </dev/null >/dev/null 2>&1; echo "  render restarted"; healthy render
P watch "$mid" 150; P inspect "$mid"
out=$(P check "$mid" delivered_full); echo "$out"; echo "$out" | grep -q "VERDICT PASS" || FAILS=$((FAILS + 1))

echo; echo "################ CASE $((N + 1)): the render AUDIO FILE cannot be read (section 7)"
N=$((N + 1)); echo "  DESIGN SAYS: the rendering is kept as TEXT with degraded_reason=tts_skipped; a missing voice never costs the text"
cat > /tmp/degrade_override.yml <<'YML'
services:
  gateway:
    environment:
      AI_RENDER_AUDIO_ROOT: /nonexistent-render-output
YML
$DC -f /tmp/degrade_override.yml up -d --no-deps --force-recreate gateway </dev/null >/dev/null 2>&1
healthy gateway
$DC exec -T gateway python -c "from app.config import get_settings as g; print('  AI_RENDER_AUDIO_ROOT is now:', g().AI_RENDER_AUDIO_ROOT)" </dev/null 2>&1 | tail -1
P seed >/dev/null
mid=$(P send_text | tail -1 | tr -d '\r'); echo "  message id: $mid"
P watch "$mid" 90; P inspect "$mid"
out=$(P check "$mid" delivered_text_only); echo "$out"; echo "$out" | grep -q "VERDICT PASS" || FAILS=$((FAILS + 1))

echo; echo "################ SUMMARY: $((N - FAILS)) of $N cases passed"
echo "################ teardown (the throwaway project only)"
$DC down -v </dev/null 2>&1 | tail -1
cd ~/veerendra && git worktree remove --force ~/wt-degrade; git worktree prune; rm -f /tmp/degrade_override.yml
echo "left over: $(docker ps -a --format '{{.Names}}' | grep -c '^degrade-') containers, $(docker volume ls -q | grep -c '^degrade_') volumes"
echo DEGRADE_PROOF_DONE
[ "$FAILS" = 0 ]
