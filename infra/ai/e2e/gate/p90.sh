#!/usr/bin/env bash
# 30-second notes on the GATE stack, one at a time (service time, not queueing).
#   N notes per language (default 8), each sent only when the previous one was seen by every reader.
#   UNDO_WINDOW_SECONDS=0, so the number is PIPELINE time (what an elder waits is at least the
#   undo window, 30 s by default, so user-visible = max(30 s, this)). Stopgap on (a Chrome note needs it).
set -uo pipefail
N="${N:-8}"
cd ~/wt-gate || exit 1
DC="docker compose -p gate -f docker-compose.yml -f docker-compose.ai.yml"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2); PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
echo "=== 1. gateway: undo window 0, stopgap on"
UNDO_WINDOW_SECONDS=0 bash ~/gate-walk/stopgap.sh on 2>&1 | grep -E "gateway:|PIPELINE"
E2E=~/gate-walk/e2e; mkdir -p "$E2E/samples" "$E2E/logs"; rm -f "$E2E"/logs/*
sed -i 's/duration_ms": 5000/duration_ms": int(os.environ.get("DUR_MS", "5000"))/' "$E2E/real_driver.py"
grep -q '^import os' "$E2E/real_driver.py" || sed -i '0,/^import json/s//import json\nimport os/' "$E2E/real_driver.py"
echo "=== 2. samples: ~30 s of DIFFERENT sentences per language (make_long_speech.py; synthetic speech)"
# (A first version looped one sentence six times; the ASR collapsed it to ~one phrase, so that
# stimulus timed nothing like 30 s of speech. Never loop.)
RID=$($DC ps -q render)
LONG=$($DC exec -T render python - < ~/gate-walk/make_long_speech.py); echo "$LONG"
for l in hi te; do docker cp "$RID:/render-output/long-$l.wav" ~/gate-samples/long-$l.wav; done
docker run --rm -v ~/gate-samples:/out python:3.11-slim sh -c '
  apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq --no-install-recommends ffmpeg >/dev/null 2>&1
  for l in hi te; do ffmpeg -nostdin -loglevel error -y -i /out/long-$l.wav -c:a libopus -f webm /out/real_${l}30.webm; done
  python - <<PY
import wave
for l in ("hi", "te"):
    w = wave.open("/out/long-%s.wav" % l); ms = round(1000 * w.getnframes() / w.getframerate())
    print("%s note: %d ms" % (l, ms)); open("/out/dur_%s30.txt" % l, "w").write(str(ms))
PY
  chmod a+r /out/*; ls -l /out/real_*30.webm'
cp ~/gate-samples/real_hi30.webm ~/gate-samples/real_te30.webm "$E2E/samples/"
DUR_HI=$(cat ~/gate-samples/dur_hi30.txt); DUR_TE=$(cat ~/gate-samples/dur_te30.txt)
sha256sum ~/gate-samples/real_hi30.webm ~/gate-samples/real_te30.webm | sed 's#  .*/#  #'
echo "=== 3. four users and a circle for the driver"
psql_q "INSERT INTO users (id,name,preferred_language,role) VALUES
 ('00000000-0000-4000-8000-000000000001','Alice (author)','te','elder'),
 ('00000000-0000-4000-8000-000000000002','Bob (reads Telugu)','te','elder'),
 ('00000000-0000-4000-8000-000000000003','Carol (reads English)','en','elder'),
 ('00000000-0000-4000-8000-000000000004','Dan (reads Hindi)','hi','elder')
 ON CONFLICT (id) DO UPDATE SET preferred_language = EXCLUDED.preferred_language" >/dev/null
drv() { docker run --rm --network gate_default -e DUR_MS="${DUR:-5000}" -v "$E2E":/e2e gate-gateway python /e2e/real_driver.py "$@"; }
CIRCLE=$(drv circle | sed -n 's/.*"circle_id": "\([^"]*\)".*/\1/p'); echo "circle=$CIRCLE"
[ -n "$CIRCLE" ] || { echo "no circle"; exit 1; }
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "=== 4. $N notes per language, one at a time (start $START)"
for spec in "hi30:hi:$DUR_HI" "te30:te:$DUR_TE"; do
  name=${spec%%:*}; rest=${spec#*:}; lang=${rest%%:*}; dur=${rest#*:}
  for i in $(seq 1 "$N"); do
    DUR=$dur drv burst "$CIRCLE" 420 1 "/e2e/samples/real_${name}.webm:${name}:${lang}" > "$E2E/logs/${name}_$i.jsonl" 2>&1
    echo "$name #$i: $(grep -o '"wall_s": {[^}]*}' "$E2E/logs/${name}_$i.jsonl" | head -1) $(grep -c never_visible "$E2E/logs/${name}_$i.jsonl") never_visible"
  done
done
echo "=== 5. summary (load_stats.summarize over the per-note wall times; a note nobody saw is NOT dropped, it is counted)"
docker run --rm -i -v "$E2E":/e2e gate-gateway python - <<'PY'
import glob, json, sys
sys.path.insert(0, "/e2e")
from load_stats import summarize
for name in ("hi30", "te30"):
    walls, missing = [], 0
    for f in sorted(glob.glob(f"/e2e/logs/{name}_*.jsonl")):
        for line in open(f, encoding="utf-8"):
            if '"burst_summary"' in line:
                d = json.loads(line)
                w = d.get("wall_s", {})
                if w.get("n"):
                    walls.append(w["p50"])
                else:
                    missing += 1
    print(name, "service time (s) per note:", sorted(walls), "| never visible:", missing)
    print(name, summarize(walls))
PY
echo "=== 6. what the gateway recorded since $START"
psql_q "SELECT 'messages '||status||' pipeline='||coalesce(pipeline_state,'NULL')||' = '||count(*) FROM messages WHERE created_at >= '$START' GROUP BY status,pipeline_state"
psql_q "SELECT 'jobs '||status||' most attempts used='||max(attempts)||' = '||count(*) FROM jobs WHERE job_type='process_message' AND created_at >= '$START' GROUP BY status"
psql_q "SELECT 'events '||actor_kind||' '||action||' = '||count(*) FROM moderation_events e JOIN messages m ON m.id=e.message_id WHERE m.created_at >= '$START' GROUP BY actor_kind, action"
echo "gateway log lines (timeout / retry / lease):"; $DC logs --since "$START" gateway 2>&1 | grep -iE "timeout|retry|no longer held|TIMEOUT|Traceback" | sed -E 's/hf_[A-Za-z0-9]{10,}/hf_REDACTED/g' | sort | uniq -c | head -10
echo GATE_P90_DONE
