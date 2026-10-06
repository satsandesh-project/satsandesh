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
# UNDO_WINDOW_SECONDS=0 on purpose. With a 30 s window a fast pipeline simply
# finishes first and nothing is learned (an earlier run with a 2 s window did
# exactly that: the CPU pipeline finished inside it). With 0, the scheduled
# fan-out fires the instant the message exists -- BEFORE the pipeline can have
# finished -- so a recipient sees the message only if the delivery gate holds
# it back and the job then delivers it itself.
set -uo pipefail
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1

P=aie2e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.." || exit 1
[ -f .env ] || { echo "no .env in $(pwd)"; exit 1; }

export POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-55433}"
export AI_PIVOT_URL=http://ai-mock:8001 AI_RENDER_URL=http://ai-mock:8001
export UNDO_WINDOW_SECONDS=0 JOB_LEASE_SECONDS=20 JOB_MAX_ATTEMPTS=3 AI_ACCEPT_WEBM_AS_OGG_OPUS=true
export PIPELINE_ENABLED=true
# The proof runs under the REAL token verification: the gateway in `jwt` mode (a bare UUID is a
# 401) and every user holds a SIGNED token minted below. AUTH_MODE=legacy to compare.
export AUTH_MODE="${AUTH_MODE:-jwt}"

DC="docker compose -p $P -f docker-compose.yml -f docker-compose.ai.yml --profile mock"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2)
PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
ALICE=00000000-0000-4000-8000-000000000001
BOB=00000000-0000-4000-8000-000000000002
MOD=00000000-0000-4000-8000-000000000003

psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
mint() { $DC exec -T gateway python -m app.tokens "$1" < /dev/null | tail -1; }
driver() { docker run --rm --network "${P}_default" -e TOKEN_ALICE -e TOKEN_BOB -e TOKEN_MOD -v "$HERE":/e2e "${P}-gateway" python /e2e/proof_driver.py "$@"; }
say()    { printf '\n=== %s\n' "$*"; }
field()  { sed -n "s/.*\"$1\": \"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" | head -1; }
send()   {  # send <file> <label>: prints the event, sets MSG and SENT_AT
  local out; out=$(driver send "/e2e/samples/$1" "$2" en); echo "$out"
  MSG=$(echo "$out" | field message_id); SENT_AT=$(echo "$out" | field sent_at)
}
job_state() { psql_q "SELECT status||' (attempts='||attempts||'/'||max_attempts||')' FROM jobs WHERE payload->>'message_id'='$1'"; }
msg_state() { psql_q "SELECT status||' / pipeline_state='||coalesce(pipeline_state,'NULL') FROM messages WHERE id='$1'"; }
healthy() {  # wait for a service's container to report healthy
  local svc=$1 limit=${2:-90} cid st
  for _ in $(seq 1 "$limit"); do
    cid=$($DC ps -q "$svc"); st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)
    [ "$st" = healthy ] && return 0; sleep 5
  done
  echo "$svc never became healthy; container state:"
  docker inspect -f '  status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{end}} restarts={{.RestartCount}} exit={{.State.ExitCode}}' "$cid" 2>&1
  $DC logs --tail 30 "$svc"; return 1
}
cleanup() { if [ "$KEEP" = 0 ]; then $DC down -v >/dev/null 2>&1; echo "(stack removed)"; else echo "(stack left running: project $P)"; fi; }
trap cleanup EXIT

say "0. samples"
# `bash`, not the file's own executable bit: a checkout from Windows (or `core.fileMode=false`)
# has none, and a proof whose recordings were never made is not a proof.
[ -s "$HERE/samples/silence.webm" ] || { mkdir -p "$HERE/samples"; bash "$HERE/make_samples.sh" "$HERE/samples"; }
for f in speech.webm silence.webm; do
  [ -s "$HERE/samples/$f" ] || { echo "PRECONDITION FAILED: $HERE/samples/$f was not made; this proof would prove nothing"; exit 1; }
done
ls -l "$HERE/samples"

say "1. bring the stack up (own Postgres, own volumes; project $P)"
$DC up -d --build postgres gateway speech moderation ai-mock 2>&1 | tail -3
healthy postgres 20 && healthy moderation 20 && healthy ai-mock 20 && healthy gateway 40 || exit 1
echo "waiting for the ASR model to download and warm up (first start only)..."
healthy speech 120 || exit 1
echo "gateway pipeline env:"; $DC exec -T gateway sh -c 'env | grep -E "^(PIPELINE|AI_|UNDO|JOB_)" | sort'

say "2. the three users (inserted directly: the stub auth only flushes a new user row, so a request that ends in 403 rolls it back and cannot provision a moderator)"
psql_q "INSERT INTO users (id,name,preferred_language,role) VALUES
  ('$ALICE','Alice (author)','en','elder'),
  ('$BOB','Bob (recipient)','hi','elder'),
  ('$MOD','Moderator','en','moderator')
  ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, preferred_language = EXCLUDED.preferred_language" >/dev/null
psql_q "SELECT '  '||name||' role='||role||' language='||preferred_language FROM users ORDER BY name"
# The users exist now (a signed token for a user with no row is a 401), so mint their tokens.
export TOKEN_ALICE=$(mint $ALICE) TOKEN_BOB=$(mint $BOB) TOKEN_MOD=$(mint $MOD)
echo "auth: AUTH_MODE=$AUTH_MODE, signed tokens minted for 3 users (${#TOKEN_ALICE} chars each)"
driver queue

say "3. SCENARIO A: real speech. The undo window is 0, so delivery is attempted at once; it must WAIT for the pipeline"
send speech.webm speech; A=$MSG
echo "state immediately after sending: $(msg_state "$A")   job: $(job_state "$A")"
driver watch "$A" 240 "$SENT_AT"
driver mine "$A"
driver events "$A"

say "4. SCENARIO B: digital silence -> no speech to rule on"
send silence.webm silence; B=$MSG
driver watch "$B" 40 "$SENT_AT"
echo "message: $(msg_state "$B")   job: $(job_state "$B")"
driver events "$B"

say "5. SCENARIO C: SIGKILL the gateway while the job is running, then restart"
send speech.webm speech-crash; C=$MSG; C_SENT=$SENT_AT
echo "waiting for the job to be RUNNING..."
for _ in $(seq 1 80); do
  [ "$(psql_q "SELECT status FROM jobs WHERE payload->>'message_id'='$C'")" = running ] && break; sleep 0.25
done
echo "job at the moment of the kill:     $(job_state "$C")"
echo "message at the moment of the kill: $(msg_state "$C")"
GW=$($DC ps -q gateway)
$DC kill -s SIGKILL gateway >/dev/null
sleep 3
echo "right after the kill: $(docker inspect -f 'status={{.State.Status}} exit={{.State.ExitCode}} restarts={{.RestartCount}}' "$GW")"
$DC start gateway >/dev/null 2>&1
healthy gateway 60 || exit 1
echo "gateway back; the 20s lease must expire before another worker may reclaim the job"
driver watch "$C" 240 "$C_SENT"
driver events "$C"
echo "job after recovery:   $(job_state "$C")"
echo "classifier rulings:   $(psql_q "SELECT count(*) FROM moderation_events WHERE message_id='$C' AND actor_kind='classifier'")  (must be exactly 1)"

say "6. SCENARIO D: the moderation service is DOWN. Nothing may be delivered unmoderated; a person decides"
$DC stop moderation >/dev/null 2>&1
echo "moderation stopped: $(docker inspect -f '{{.State.Status}}' "$($DC ps -a -q moderation)")"
send speech.webm moderation-down; D=$MSG; D_SENT=$SENT_AT
echo "JOB_MAX_ATTEMPTS=3, backoff 5s then 10s: waiting for the job to die..."
for _ in $(seq 1 60); do
  [ "$(psql_q "SELECT status FROM jobs WHERE payload->>'message_id'='$D'")" = dead ] && break; sleep 2
done
echo "job:     $(job_state "$D")"
echo "message: $(msg_state "$D")"
echo "last job error: $(psql_q "SELECT left(last_error,160) FROM jobs WHERE payload->>'message_id'='$D'")"
echo "-- does Bob (the recipient) see it? --"
driver watch "$D" 8 "$D_SENT"
echo "-- the moderator's queue --"
driver queue
driver events "$D"
$DC start moderation >/dev/null 2>&1
healthy moderation 20 || exit 1
echo "-- moderation is back; the moderator releases it --"
driver release "$D" | cut -c1-220
driver watch "$D" 20 "$D_SENT"
driver events "$D"

say "7. database summary"
psql_q "SELECT 'messages  '||status||' / pipeline='||coalesce(pipeline_state,'NULL')||' = '||count(*) FROM messages GROUP BY status,pipeline_state ORDER BY 1"
psql_q "SELECT 'jobs      '||job_type||' '||status||' (most attempts used: '||max(attempts)||') = '||count(*) FROM jobs GROUP BY job_type,status ORDER BY 1"
psql_q "SELECT 'events    '||actor_kind||' '||action||' = '||count(*) FROM moderation_events GROUP BY actor_kind,action ORDER BY 1"
psql_q "SELECT 'duplicate jobs per message (must be 0): '||count(*) FROM (SELECT payload->>'message_id' FROM jobs WHERE job_type='process_message' GROUP BY 1 HAVING count(*)>1) x"
echo; echo "PROOF_DONE"
