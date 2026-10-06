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
#
# LOAD MODE:  infra/ai/e2e/run_proof_real.sh --load "1 1 3 10" [--keep]
#   Instead of steps 4-5, runs one burst per depth (N notes dispatched together). A depth is N,
#   cycling the samples in LOAD_SAMPLES (default "hi te"), or N:hi / N:te / N:hi+te to choose the
#   mix for that depth; LOAD_TIMEOUT seconds per depth, default 900. For each depth it records, in $LOAD_DIR (default /tmp/aireal-load):
#   the driver's per-note events and summary, the job queue once a second (depth, the running
#   job's remaining lease), the host (vmstat 1) and every container (docker stats), then prints
#   a report. Before the first depth it prints each container's CPU limit as seen from INSIDE
#   it (cpu.max, Cpus_allowed_list): this script sets NO limit, and says so.
set -uo pipefail
KEEP=0; LOAD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1 ;;
    --load) shift; LOAD="${1:-}" ;;
    *) echo "unknown argument: $1"; exit 2 ;;
  esac
  shift
done

P=aireal
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../../.." || exit 1
[ -f .env ] || { echo "no .env in $(pwd)"; exit 1; }
grep -q '^HF_TOKEN=.' .env || { echo "HF_TOKEN is not set in .env (needed for mt and render)"; exit 1; }

export POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-55433}"
export UNDO_WINDOW_SECONDS=0 JOB_LEASE_SECONDS=60 JOB_MAX_ATTEMPTS=3 AI_ACCEPT_WEBM_AS_OGG_OPUS=true
export PIPELINE_ENABLED=true
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
driver() { docker run --rm --network "${P}_default" -v "$HERE":/e2e "${P}-gateway" python /e2e/real_driver.py "$@"; }
say()    { printf '\n=== %s\n' "$*"; }
healthy() {
  local svc=$1 limit=${2:-90} cid st
  for _ in $(seq 1 "$limit"); do
    cid=$($DC ps -q "$svc"); st=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null)
    [ "$st" = healthy ] && return 0; sleep 5
  done
  echo "$svc never became healthy:"; $DC logs --tail 30 "$svc" 2>&1 | sed -E 's/hf_[A-Za-z0-9]{10,}/hf_REDACTED/g'; return 1
}
# ---- load mode helpers -------------------------------------------------------------------
LOAD_DIR="${LOAD_DIR:-/tmp/aireal-load}"
cpu_precondition() {
  echo "host: $(nproc) CPUs, $(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo) MiB, $(awk '/SwapTotal/ {printf "%d", $2/1024}' /proc/meminfo) MiB swap; load $(cut -d' ' -f1-3 /proc/loadavg)"
  for svc in gateway speech mt render moderation postgres; do
    cid=$($DC ps -q "$svc")
    [ -n "$cid" ] || { echo "  $svc: NOT RUNNING"; continue; }
    echo "  $svc: HostConfig NanoCpus=$(docker inspect -f '{{.HostConfig.NanoCpus}}' "$cid") CpuQuota=$(docker inspect -f '{{.HostConfig.CpuQuota}}' "$cid") Memory=$(docker inspect -f '{{.HostConfig.Memory}}' "$cid") | inside: $(docker exec "$cid" sh -c 'echo "cpu.max=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo "v1 quota=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us 2>/dev/null)") memory.max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null) Cpus_allowed_list=$(grep Cpus_allowed_list /proc/self/status | cut -f2)"')"
  done
  echo "  (cpu.max 'max ...' and NanoCpus=0 mean NO limit is enforced: every number below is unconstrained on a shared host)"
}
sample_jobs() {  # $1 = out file. One line a second: epoch | queued running done dead | running job id + lease seconds left
  while :; do
    row=$(psql_q "SELECT count(*) FILTER (WHERE status='queued')||' '||count(*) FILTER (WHERE status='running')||' '||count(*) FILTER (WHERE status='done')||' '||count(*) FILTER (WHERE status='dead')||' | '||coalesce((SELECT id||' '||round(extract(epoch FROM lease_expires_at-now())::numeric) FROM jobs WHERE status='running' ORDER BY lease_expires_at LIMIT 1),'- -') FROM jobs" 2>/dev/null </dev/null)
    echo "$(date +%s) | $row" >> "$1"; sleep 1
  done
}
# Per-service CPU and memory: the host processes that descend from each container's init pid
# (`docker inspect` .State.Pid), summed from `ps`. NOT docker stats, NOT the cgroup files, NOT
# `docker top`: on this host the containers share one cgroup, so docker stats and the cgroup
# files report the same number for every container (a first version of this script printed that as
# if it were per-service) and `docker top` fails in runc. cputimes is whole seconds, and a child
# that starts and exits between two samples (ffmpeg) is not seen, so CPU here is a lower bound.
sample_procs() {  # $1 = out file, then "service:init-pid" pairs. Lines: epoch service rss_mib cpu_seconds, and epoch host avail_mib swap_used_mib
  local out=$1; shift
  local pairs="$*" now
  while :; do
    now=$(date +%s)
    ps -e -o pid=,ppid=,rss=,cputimes= | awk -v now="$now" -v pairs="$pairs" '
      { ppid[$1]=$2; rss[$1]=$3; cpu[$1]=$4; pids[n++]=$1 }
      END {
        m = split(pairs, a, " ")
        for (i = 1; i <= m; i++) { split(a[i], kv, ":"); root[kv[1]] = kv[2] }
        for (sv in root) {
          r = 0; c = 0
          for (j = 0; j < n; j++) {
            p = pids[j]; q = p
            while (q > 1) { if (q == root[sv]) { r += rss[p]; c += cpu[p]; break } q = ppid[q] }
          }
          print now, sv, int(r / 1024), c
        }
      }' >> "$out"
    echo "$now host $(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo) $(awk '/SwapTotal/ {t=$2} /SwapFree/ {f=$2} END {printf "%d", (t-f)/1024}' /proc/meminfo)" >> "$out"
    sleep 1
  done
}
report_depth() {  # $1 depth  $2 dir  $3 start (iso, utc)  $4 start epoch
  local n=$1 d=$2 start=$3 t0=$4
  echo; echo "=== REPORT depth $n  (files: $d)"
  for f in driver.jsonl jobs.txt vmstat.txt procs.txt; do
    echo "  evidence $f: $(wc -l < "$d/$f") lines"
  done
  grep -q '"event": "burst_summary"' "$d/driver.jsonl" || echo "  !! NO burst_summary in the driver output: this depth is VOID"
  echo "-- driver summary:"; grep '"event": "burst_summary"' "$d/driver.jsonl"
  echo "-- dispatch spread: $(grep dispatch_spread_s "$d/driver.jsonl")"
  echo "-- per-note send acks that were not 200/201:"; grep '"event": "send_failed"' "$d/driver.jsonl" || echo "   none"
  echo "-- queue (jobs.txt): first/last sample and a timeline every 5 s (t, queued, running, done, dead)"
  awk -F'|' -v t0="$t0" '
    { split($2,a," "); split($3,b," "); n++; dep=a[1]+a[2]; if(dep>m)m=dep; if(a[2]>r)r=a[2];
      if(b[1]!="-"){ left=b[2]+0; if(!seen||left<ml)ml=left; if(seen&&b[1]==pj&&left>pl+5)ren++; pj=b[1]; pl=left; seen=1 }
      if(n%5==1) printf "   t+%ss  queued=%s running=%s done=%s dead=%s\n", $1-t0, a[1], a[2], a[3], a[4] }
    END { printf "   samples=%d  max(queued+running)=%d  max running=%d  closest approach of a running lease to expiry=%ss  lease renewals seen (same job, remaining time jumped up)=%d\n", n, m, r, ml, ren+0 }' "$d/jobs.txt"
  echo "-- host (vmstat 1; includes the OTHER stacks on this shared server):"
  awk '$1 ~ /^[0-9]+$/ && $15 ~ /^[0-9]+$/ && NR>3 { n++; if(min==""||$15+0<min+0)min=$15; if($13+$14>mx)mx=$13+$14; if($7+0>si+0)si=$7; if($8+0>so+0)so=$8; if($3+0>sw+0)sw=$3 }
       END { printf "   samples=%d  min idle=%s%%  max us+sy=%s%%  max swapped=%d MiB  max si=%s so=%s (KiB/s)\n", n, min, mx, sw/1024, si+0, so+0 }' "$d/vmstat.txt"
  awk '$2=="host" { n++; if(av==""||$3<av)av=$3; if($4>sw)sw=$4 } END { printf "   (sampled with the services) samples=%d  min MemAvailable=%d MiB  max swap in use=%d MiB\n", n, av, sw }' "$d/procs.txt"
  echo "-- services (process tree under each container's init pid; CPU is a LOWER bound, see sample_procs):"
  awk -v hz=1 '$2!="host" { s=$2; if(!(s in t0)) t0[s]=$1; t1[s]=$1; if($3>pk[s])pk[s]=$3;
         if((s in pt) && $4>pt[s]) { cpu[s]+=($4-pt[s])/hz; if($1>pn[s]) { r=($4-pt[s])/hz/($1-pn[s]); if(r>pr[s])pr[s]=r } } pt[s]=$4; pn[s]=$1 }
       END { for(s in pk) printf "   %-11s peak RSS %6d MiB   CPU %7.1f s over %ds = %.2f cores on average   busiest 1-3 s window %.1f cores\n", s, pk[s], cpu[s], t1[s]-t0[s], cpu[s]/(t1[s]-t0[s]+1), pr[s] }' "$d/procs.txt" | sort
  echo "-- containers: OOMKilled / restarts (after):"
  for svc in gateway speech mt render moderation; do
    cid=$($DC ps -q "$svc"); echo "   $svc OOMKilled=$(docker inspect -f '{{.State.OOMKilled}}' "$cid") restarts=$(docker inspect -f '{{.RestartCount}}' "$cid")"
  done
  echo "-- gateway log lines (since this depth) about a lost lease / failed heartbeat / AI timeout / error:"
  $DC logs --since "$start" gateway 2>&1 | grep -iE 'no longer held|heartbeat failed|timed? ?out|TIMEOUT|Traceback|ERROR' | sed -E 's/hf_[A-Za-z0-9]{10,}/hf_REDACTED/g' | sort | uniq -c | head -12 || true
  echo "   (end of list)"
  echo "-- AI services log lines (since this depth) with Traceback / ERROR / Killed:"
  for svc in speech mt render moderation; do
    echo "   $svc: $($DC logs --since "$start" "$svc" 2>&1 | grep -ciE 'Traceback|ERROR|Killed')"
  done
  echo "-- database, messages and jobs created since this depth started:"
  psql_q "SELECT '   messages = '||count(*)||'  statuses: '||string_agg(DISTINCT status,',') FROM messages WHERE created_at >= '$start'"
  psql_q "SELECT '   pipeline_state '||coalesce(pipeline_state,'NULL')||' = '||count(*) FROM messages WHERE created_at >= '$start' GROUP BY pipeline_state ORDER BY 1"
  psql_q "SELECT '   DELIVERED WITHOUT A CLASSIFIER EVENT = '||count(*) FROM messages m WHERE m.created_at >= '$start' AND m.status IN ('sent','delivered') AND NOT EXISTS (SELECT 1 FROM moderation_events e WHERE e.message_id=m.id AND e.actor_kind='classifier')"
  psql_q "SELECT '   messages with MORE THAN ONE classifier event = '||count(*) FROM (SELECT e.message_id FROM moderation_events e JOIN messages m ON m.id=e.message_id WHERE m.created_at >= '$start' AND e.actor_kind='classifier' GROUP BY e.message_id HAVING count(*)>1) x"
  psql_q "SELECT '   process_message jobs '||status||' = '||count(*)||'  most attempts used: '||max(attempts) FROM jobs WHERE job_type='process_message' AND created_at >= '$start' GROUP BY status ORDER BY 1"
  psql_q "SELECT '   renderings per message: '||k||' renderings x '||count(*)||' messages' FROM (SELECT count(r.message_id) AS k FROM messages m LEFT JOIN message_renderings r ON r.message_id=m.id WHERE m.created_at >= '$start' GROUP BY m.id) x GROUP BY k ORDER BY k"
}
load_depth() {
  local spec=$1 n=${1%%:*} mix d start t0
  case "$spec" in *:*) mix=${spec#*:}; mix=${mix//+/ } ;; *) mix=${LOAD_SAMPLES:-hi te} ;; esac
  d="$LOAD_DIR/depth${spec//[:+]/-}-$(date +%H%M%S)"
  mkdir -p "$d"; : > "$d/jobs.txt"; : > "$d/procs.txt"
  start=$(date -u +%Y-%m-%dT%H:%M:%SZ); t0=$(date +%s)
  echo; echo "##### depth $n ($mix): $n notes at once; host load before: $(cut -d' ' -f1-3 /proc/loadavg)"
  vmstat 1 > "$d/vmstat.txt" & local vm=$!
  sample_jobs "$d/jobs.txt" & local js=$!
  local pairs=""
  for svc in gateway speech mt render moderation postgres; do
    pairs="$pairs $svc:$(docker inspect -f '{{.State.Pid}}' "$($DC ps -q "$svc")")"
  done
  # shellcheck disable=SC2086
  sample_procs "$d/procs.txt" $pairs & local st=$!
  sleep 3   # a few quiet samples first: the baseline
  local specs=""
  for s in $mix; do specs="$specs /e2e/samples/real_$s.webm:${s}-note:$s"; done
  # shellcheck disable=SC2086
  driver burst "$CIRCLE" "${LOAD_TIMEOUT:-900}" "$n" $specs > "$d/driver.jsonl"
  sleep 3   # and a few after
  kill "$vm" "$js" "$st" 2>/dev/null; wait "$vm" "$js" "$st" 2>/dev/null
  report_depth "$spec" "$d" "$start" "$t0"
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
CIRCLE_OUT=$(driver circle); echo "$CIRCLE_OUT"
CIRCLE=$(echo "$CIRCLE_OUT" | sed -n 's/.*"circle_id": "\([^"]*\)".*/\1/p')
[ -n "$CIRCLE" ] || { echo "no circle"; exit 1; }

if [ -n "$LOAD" ]; then
  say "load: preconditions (what limits are actually enforced, seen from inside the containers)"
  cpu_precondition
  echo "samples used (Piper re-synthesizes them on every run, so the audio can differ between runs):"
  sha256sum "$HERE"/samples/real_*.webm "$HERE"/samples/real_*.wav | sed 's#  .*/#  #'
  mkdir -p "$LOAD_DIR"
  for depth in $LOAD; do load_depth "$depth"; done
else
say "4. a HINDI note to the circle: expect ASR(hi) -> MT(hi->en) -> moderate -> render: Bob gets Telugu, Carol gets English, Dan (Hindi) reads the original"
driver run /e2e/samples/real_hi.webm hindi-note hi "$CIRCLE" 300

say "5. a TELUGU note to the circle: Bob (Telugu) reads the original, Carol gets English, Dan gets Hindi"
driver run /e2e/samples/real_te.webm telugu-note te "$CIRCLE" 300
fi

say "6. database summary"
psql_q "SELECT 'messages  '||status||' / pipeline='||coalesce(pipeline_state,'NULL')||' = '||count(*) FROM messages GROUP BY status,pipeline_state ORDER BY 1"
psql_q "SELECT 'renderings '||language||' degraded='||coalesce(degraded_reason,'none')||' audio='||(audio_media_object_id IS NOT NULL)||' = '||count(*) FROM message_renderings GROUP BY language,degraded_reason,(audio_media_object_id IS NOT NULL) ORDER BY 1"
psql_q "SELECT 'jobs      '||job_type||' '||status||' (most attempts used: '||max(attempts)||') = '||count(*) FROM jobs GROUP BY job_type,status ORDER BY 1"
psql_q "SELECT 'events    '||actor_kind||' '||action||' degraded='||degraded||' = '||count(*) FROM moderation_events GROUP BY actor_kind,action,degraded ORDER BY 1"
psql_q "SELECT 'transcript ['||coalesce(transcript_language,'?')||'] '||coalesce(transcript,'NULL')||'  | pivot_en: '||coalesce(pivot_text_en,'NULL') FROM messages WHERE kind='voice' ORDER BY created_at"
echo; echo "PROOF_DONE"
