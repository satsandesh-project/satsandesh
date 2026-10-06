#!/usr/bin/env bash
# End-to-end proof of the pipeline against the REAL ASR, MT and render services
# (the moderation service is still its STUB keyword backend). Companion to
# run_proof.sh, which proves delivery/crash/failure behaviour with MT and render
# as a MOCK; this one proves that "a note becomes N renderings" with nothing
# canned in the translation or the speech.
#
#   usage (from the repo root, with a .env in it that has HF_TOKEN):
#       infra/ai/e2e/run_proof_real.sh [--keep]
#
# Own compose project (`aireal`) with its own Postgres and volumes; touches no
# shared stack. First run downloads the gated models (needs HF_TOKEN in .env,
# and the account must have accepted the gate on BOTH IndicTrans2 repos).
#
# What it does:
#   1. the author's "voice" is made by the real render service's own Piper voices
#      (a Hindi and a Telugu sentence), converted to WebM/Opus -- synthetic speech
#      from the same voices the pipeline renders with, NOT a person on a
#      microphone. It proves the stages connect; it says nothing about accuracy
#      on an elder's voice.
#   2. a circle with three readers: Telugu (bob), English (carol), Hindi (dan)
#   3. a Hindi note and a Telugu note are sent to the circle; for each reader the
#      script records when it became visible, the transcript, every rendering's
#      text, and the rendering AUDIO fetched through the gateway as that reader
#      (size, seconds, loudness).
#
# UNDO_WINDOW_SECONDS=0 and AI_ACCEPT_WEBM_AS_OGG_OPUS=true, as in run_proof.sh
# (the second is the opt-in stopgap for the browser's webm_opus; OPEN_QUESTIONS #11).
set -uo pipefail
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1

P=aireal
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.." || exit 1
[ -f .env ] || { echo "no .env in $(pwd)"; exit 1; }
grep -q '^HF_TOKEN=.' .env || { echo "HF_TOKEN is not set in .env (needed for mt and render)"; exit 1; }

export POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-55433}"
export UNDO_WINDOW_SECONDS=0 JOB_LEASE_SECONDS=60 JOB_MAX_ATTEMPTS=3 AI_ACCEPT_WEBM_AS_OGG_OPUS=true
export PIPELINE_ENABLED=true
# The proof runs under the REAL token verification: the gateway in `jwt` mode (a bare UUID is a
# 401) and every user holds a SIGNED token minted below. AUTH_MODE=legacy to compare.
export AUTH_MODE="${AUTH_MODE:-jwt}"
# deliberately NOT exporting AI_PIVOT_URL / AI_RENDER_URL: the compose defaults point at the
# real mt and render services.

DC="docker compose -p $P -f docker-compose.yml -f docker-compose.ai.yml"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2)
PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
ALICE=00000000-0000-4000-8000-000000000001
BOB=00000000-0000-4000-8000-000000000002
CAROL=00000000-0000-4000-8000-000000000003
DAN=00000000-0000-4000-8000-000000000004

psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
mint() { $DC exec -T gateway python -m app.tokens "$1" < /dev/null | tail -1; }
driver() { docker run --rm --network "${P}_default" -e TOKEN_ALICE -e TOKEN_BOB -e TOKEN_CAROL -e TOKEN_DAN -v "$HERE":/e2e "${P}-gateway" python /e2e/real_driver.py "$@"; }
say()    { printf '\n=== %s\n' "$*"; }
healthy() {
  local svc=$1 limit=${2:-90} cid st
  for _ in $(seq 1 "$limit"); do
    cid=$($DC ps -q "$svc"); st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)
    [ "$st" = healthy ] && return 0; sleep 5
  done
  echo "$svc never became healthy:"; $DC logs --tail 30 "$svc" 2>&1 | sed -E 's/hf_[A-Za-z0-9]{10,}/hf_REDACTED/g'; return 1
}
cleanup() { if [ "$KEEP" = 0 ]; then $DC down -v >/dev/null 2>&1; echo "(stack removed)"; else echo "(stack left running: project $P)"; fi; }
trap cleanup EXIT

say "1. bring the stack up (own Postgres + volumes, project $P)"
$DC up -d --build postgres gateway speech moderation mt render 2>&1 | tail -4
healthy postgres 20 && healthy moderation 20 && healthy gateway 40 || exit 1
echo "waiting for the ASR, MT and render models (first start downloads them)..."
healthy speech 120 && healthy mt 120 && healthy render 120 || exit 1
echo "gateway pipeline env:"; $DC exec -T gateway sh -c 'env | grep -E "^(PIPELINE|AI_|UNDO|JOB_)" | sort'

say "2. the speech samples (made by the real render service, see the header)"
mkdir -p "$HERE/samples"
JSON=$($DC exec -T render python - < "$HERE/real_make_speech.py")
echo "$JSON"
HI_WAV=$(echo "$JSON" | sed -n 's/.*"hi": "\([^"]*\)".*/\1/p')
TE_WAV=$(echo "$JSON" | sed -n 's/.*"te": "\([^"]*\)".*/\1/p')
[ -n "$HI_WAV" ] && [ -n "$TE_WAV" ] || { echo "could not make the samples"; exit 1; }
RID=$($DC ps -q render)
docker cp "$RID:/render-output/$HI_WAV" "$HERE/samples/real_hi.wav" && docker cp "$RID:/render-output/$TE_WAV" "$HERE/samples/real_te.wav" || exit 1
docker run --rm -v "$HERE/samples":/out python:3.11-slim sh -c '
  apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq --no-install-recommends ffmpeg >/dev/null 2>&1
  for l in hi te; do ffmpeg -nostdin -loglevel error -y -i /out/real_$l.wav -c:a libopus -f webm /out/real_$l.webm; done
  ls -l /out/real_*.webm'

say "3. the four users (inserted directly: the stub auth only flushes a new user row)"
psql_q "INSERT INTO users (id,name,preferred_language,role) VALUES
  ('$ALICE','Alice (author)','en','elder'),
  ('$BOB','Bob (reads Telugu)','te','elder'),
  ('$CAROL','Carol (reads English)','en','elder'),
  ('$DAN','Dan (reads Hindi)','hi','elder')
  ON CONFLICT (id) DO UPDATE SET preferred_language = EXCLUDED.preferred_language" >/dev/null
psql_q "SELECT '  '||name||' prefers '||preferred_language FROM users ORDER BY name"
export TOKEN_ALICE=$(mint $ALICE) TOKEN_BOB=$(mint $BOB) TOKEN_CAROL=$(mint $CAROL) TOKEN_DAN=$(mint $DAN)
echo "auth: AUTH_MODE=$AUTH_MODE, signed tokens minted for 4 users"
CIRCLE_OUT=$(driver circle); echo "$CIRCLE_OUT"
CIRCLE=$(echo "$CIRCLE_OUT" | sed -n 's/.*"circle_id": "\([^"]*\)".*/\1/p')
[ -n "$CIRCLE" ] || { echo "no circle"; exit 1; }

say "4. a HINDI note to the circle: expect ASR(hi) -> MT(hi->en) -> moderate -> render: Bob gets Telugu, Carol gets English, Dan (Hindi) reads the original"
driver run /e2e/samples/real_hi.webm hindi-note hi "$CIRCLE" 300

say "5. a TELUGU note to the circle: Bob (Telugu) reads the original, Carol gets English, Dan gets Hindi"
driver run /e2e/samples/real_te.webm telugu-note te "$CIRCLE" 300

say "6. database summary"
psql_q "SELECT 'messages  '||status||' / pipeline='||coalesce(pipeline_state,'NULL')||' = '||count(*) FROM messages GROUP BY status,pipeline_state ORDER BY 1"
psql_q "SELECT 'renderings '||language||' degraded='||coalesce(degraded_reason,'none')||' audio='||(audio_media_object_id IS NOT NULL)||' = '||count(*) FROM message_renderings GROUP BY language,degraded_reason,(audio_media_object_id IS NOT NULL) ORDER BY 1"
psql_q "SELECT 'jobs      '||job_type||' '||status||' (most attempts used: '||max(attempts)||') = '||count(*) FROM jobs GROUP BY job_type,status ORDER BY 1"
psql_q "SELECT 'events    '||actor_kind||' '||action||' degraded='||degraded||' = '||count(*) FROM moderation_events GROUP BY actor_kind,action,degraded ORDER BY 1"
psql_q "SELECT 'transcript ['||coalesce(transcript_language,'?')||'] '||coalesce(transcript,'NULL')||'  | pivot_en: '||coalesce(pivot_text_en,'NULL') FROM messages WHERE kind='voice' ORDER BY created_at"
echo; echo "PROOF_DONE"
