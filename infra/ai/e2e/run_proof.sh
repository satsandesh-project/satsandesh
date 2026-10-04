#!/usr/bin/env bash
# End-to-end proof of the pipeline orchestrator against REAL services, on a
# throwaway compose project (`aie2e`): its own Postgres, its own volumes, no
# published ports but Postgres on 55433. Nothing here touches the shared
# staging stack or any other member's directory.
#
#   usage (from the repo root, with a .env in it):  infra/ai/e2e/run_proof.sh [--keep]
#
# What is REAL:   the gateway (pipeline on), the ASR service (faster-whisper
#                 `small` on CPU, decoding real WebM/Opus through ffmpeg, opening
#                 the file from the shared media volume), the moderation service
#                 (its STUB backend: a keyword heuristic, not the Qwen model).
# What is a MOCK: MT and render -- both are gated on a Hugging Face token that
#                 is not on this server -- so the pivot text is canned and
#                 renderings come back text-only. The wiring is real; the
#                 translation is not.
#
# The recordings are generated (infra/ai/e2e/make_samples.sh): a robotic voice
# muxed by ffmpeg, not a person on Chrome's MediaRecorder.
#
# UNDO_WINDOW_SECONDS is set to 2 on purpose: faster-whisper on this CPU takes
# longer than that, so the pipeline OUTLASTS the undo window -- the exact
# situation the delivery gate exists for.
set -uo pipefail
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1

P=aie2e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.." || exit 1
[ -f .env ] || { echo "no .env in $(pwd)"; exit 1; }

export POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-55433}"
export AI_PIVOT_URL=http://ai-mock:8001 AI_RENDER_URL=http://ai-mock:8001
export UNDO_WINDOW_SECONDS=2 JOB_LEASE_SECONDS=20 AI_ACCEPT_WEBM_AS_OGG_OPUS=true
export PIPELINE_ENABLED=true

DC="docker compose -p $P -f docker-compose.yml -f docker-compose.ai.yml --profile mock"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2)
PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
MOD=00000000-0000-4000-8000-000000000003
ALICE=00000000-0000-4000-8000-000000000001

psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
driver() { docker run --rm --network "${P}_default" -v "$HERE":/e2e "${P}-gateway" python /e2e/proof_driver.py "$@"; }
say()    { printf '\n=== %s\n' "$*"; }
field()  { sed -n "s/.*\"$1\": \"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" | head -1; }
healthy() {  # wait for a service's container to report healthy
  local svc=$1 limit=${2:-90} cid st
  for _ in $(seq 1 "$limit"); do
    cid=$($DC ps -q "$svc"); st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)
    [ "$st" = healthy ] && return 0; sleep 5
  done
  echo "$svc never became healthy"; $DC logs --tail 30 "$svc"; return 1
}
cleanup() { if [ "$KEEP" = 0 ]; then $DC down -v >/dev/null 2>&1; echo "(stack removed)"; else echo "(stack left running: project $P)"; fi; }
trap cleanup EXIT

say "0. samples"
[ -f "$HERE/samples/speech.webm" ] || { mkdir -p "$HERE/samples"; "$HERE/make_samples.sh" "$HERE/samples"; }
ls -l "$HERE/samples"

say "1. bring the stack up (own Postgres, own volumes; project $P)"
$DC up -d --build postgres gateway speech moderation ai-mock 2>&1 | tail -3
healthy postgres 20 && healthy moderation 20 && healthy ai-mock 20 && healthy gateway 40 || exit 1
echo "waiting for the ASR model to download and warm up (first start only)..."
healthy speech 120 || exit 1
echo "gateway pipeline env:"; $DC exec -T gateway sh -c 'env | grep -E "^(PIPELINE|AI_|UNDO|JOB_LEASE)" | sort'

say "2. a moderator (the stub auth cannot grant a role; the database row can)"
driver queue >/dev/null            # first request provisions the user row
psql_q "UPDATE users SET role='moderator' WHERE id='$MOD'" >/dev/null
driver queue

say "3. SCENARIO A: real speech, pipeline outlasts the 2s undo window"
A=$(driver send /e2e/samples/speech.webm speech en | tee /dev/stderr | field message_id)
driver watch "$A" 240
driver mine "$A"
driver events "$A"

say "4. SCENARIO B: audio with NO speech -> fail closed (held for a person)"
B=$(driver send /e2e/samples/tone.webm tone en | tee /dev/stderr | field message_id)
driver watch "$B" 45
driver mine "$B"
driver queue
driver events "$B"
echo "-- a moderator releases it --"
driver release "$B" | cut -c1-200
driver watch "$B" 20
driver events "$B"

say "5. SCENARIO C: SIGKILL the gateway mid-pipeline, then restart"
C=$(driver send /e2e/samples/speech.webm speech-crash en | tee /dev/stderr | field message_id)
echo "waiting for the job to be RUNNING..."
for _ in $(seq 1 80); do
  [ "$(psql_q "SELECT status FROM jobs WHERE payload->>'message_id'='$C' LIMIT 1")" = running ] && break; sleep 0.25
done
echo "job status at the moment of the kill: $(psql_q "SELECT status||' (attempts='||attempts||')' FROM jobs WHERE payload->>'message_id'='$C'")"
echo "message at the moment of the kill:   $(psql_q "SELECT status||' / pipeline_state='||coalesce(pipeline_state,'NULL') FROM messages WHERE id='$C'")"
$DC kill -s SIGKILL gateway >/dev/null
$DC start gateway >/dev/null
healthy gateway 40 || exit 1
echo "gateway back; the 20s lease must expire before another worker may reclaim the job"
driver watch "$C" 240
driver events "$C"
echo "job after recovery:     $(psql_q "SELECT status||' (attempts='||attempts||')' FROM jobs WHERE payload->>'message_id'='$C'")"
echo "classifier rulings:     $(psql_q "SELECT count(*) FROM moderation_events WHERE message_id='$C' AND actor_kind='classifier'")  (must be exactly 1)"

say "6. database summary"
psql_q "SELECT 'messages  '||status||'='||count(*) FROM messages GROUP BY status ORDER BY 1"
psql_q "SELECT 'jobs      '||job_type||' '||status||' max_attempts_used='||max(attempts) FROM jobs GROUP BY job_type,status ORDER BY 1"
psql_q "SELECT 'events    '||actor_kind||' '||action||'='||count(*) FROM moderation_events GROUP BY actor_kind,action ORDER BY 1"
psql_q "SELECT 'duplicate jobs per message (must be 0): '||count(*) FROM (SELECT payload->>'message_id' FROM jobs WHERE job_type='process_message' GROUP BY 1 HAVING count(*)>1) x"
echo; echo "PROOF_DONE"
