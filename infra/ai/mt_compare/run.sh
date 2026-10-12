#!/usr/bin/env bash
# READ-AND-COMPARE (M1's item 5). Changes NOTHING on staging (liveapp): its containers are never touched.
# Baseline = the production code path (the repo's mt + render services, built from team/main) in a throwaway
# compose project `mtcmp`. Candidates = plain transformers in a throwaway python container with its own cache
# volume. Typed Telugu in, nothing but model weights downloaded, no text sent anywhere. Everything is torn down.
set -uo pipefail
OUT=/tmp/mtcmp_out; mkdir -p "$OUT"; rm -f "$OUT"/*.json
echo "waiting for any other throwaway stack of mine to finish, so timings are not contaminated..."
for _ in $(seq 1 360); do
  n=$(docker ps --format '{{.Names}}' | grep -c -E '^(degrade|routetest|modauth|onbwalk|asrtime)-' || true)
  [ "$n" = 0 ] && break; sleep 10
done
echo "other throwaway containers running: $(docker ps --format '{{.Names}}' | grep -c -E '^(degrade|routetest|modauth|onbwalk|asrtime)-' || true)"
echo "host: $(nproc) cpus, $(free -g | awk '/Mem:/{print $7}') GiB available, load average $(cut -d' ' -f1-3 /proc/loadavg)"

echo; echo "################ BASELINE: the production mt + render services (IndicTrans2 200M distilled)"
cd ~/veerendra && git fetch -q team && rm -rf ~/wt-mtcmp 2>/dev/null; git worktree prune
git worktree add -q --detach ~/wt-mtcmp team/main || exit 1
cp ~/veerendra/.env ~/wt-mtcmp/.env; cd ~/wt-mtcmp || exit 1
echo "built from team/main $(git rev-parse --short HEAD)"
DC="docker compose -p mtcmp -f docker-compose.yml -f docker-compose.ai.yml"
$DC up -d --build mt render </dev/null > /tmp/mtcmp_build.log 2>&1; tail -2 /tmp/mtcmp_build.log | cut -c1-120
for s in mt render; do for _ in $(seq 1 240); do [ "$(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q $s </dev/null)" 2>/dev/null)" = healthy ] && break; sleep 5; done; echo "  $s: $(docker inspect -f '{{.State.Health.Status}}' "$($DC ps -q $s </dev/null)" 2>/dev/null)"; done
MT=$($DC ps -q mt </dev/null); RN=$($DC ps -q render </dev/null)
cat > /tmp/mtcmp_base.py <<'PY'
import json, sys, time, urllib.request
sents = json.load(open("/tmp/sentences.json", encoding="utf8"))
def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time(); d = json.loads(urllib.request.urlopen(req, timeout=600).read().decode()); return d, round(time.time() - t0, 2)
which = sys.argv[1]
out = []
if which == "mt":
    post("http://localhost:8004/v1/pivot", {"text": sents[0]["te"], "source_language": "te"})  # warm-up
    for s in sents:
        d, t = post("http://localhost:8004/v1/pivot", {"text": s["te"], "source_language": "te"})
        out.append({"id": s["id"], "en": d["pivot_text"], "t_en_s": t})
else:
    en = {r["id"]: r["en"] for r in json.load(open("/tmp/base_en.json", encoding="utf8"))}
    post("http://localhost:8005/v1/render", {"pivot_text": en[1], "target_languages": ["hi"]})  # warm-up
    for s in sents:
        d, t = post("http://localhost:8005/v1/render", {"pivot_text": en[s["id"]], "target_languages": ["hi"]})
        r = d["results"][0]
        out.append({"id": s["id"], "hi": r["text"], "t_render_total_s": t, "render_reported_ms": r.get("duration_ms")})
json.dump(out, open("/tmp/base_%s.json" % which, "w", encoding="utf8"), ensure_ascii=False)
print("ok", which, len(out))
PY
for c in "$MT" "$RN"; do docker cp "$HOME/mt_sentences.json" "$c:/tmp/sentences.json"; docker cp /tmp/mtcmp_base.py "$c:/tmp/mtcmp_base.py"; done
docker exec "$MT" python /tmp/mtcmp_base.py mt </dev/null | tail -1
docker cp "$MT:/tmp/base_mt.json" "$OUT/base_mt.json"; docker cp "$OUT/base_mt.json" "$RN:/tmp/base_en.json"
docker exec "$RN" python /tmp/mtcmp_base.py render </dev/null | tail -1
docker cp "$RN:/tmp/base_render.json" "$OUT/base_render.json"
$DC down -v </dev/null 2>&1 | tail -1

echo; echo "################ CANDIDATES (CPU, plain transformers, ${MT_THREADS:-8} threads)"
if ! docker image inspect mtcmp-py >/dev/null 2>&1; then
  cat > /tmp/Dockerfile.mtcmp <<'DF'
FROM python:3.11-slim
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch "transformers>=4.44,<5" sentencepiece protobuf accelerate sacrebleu
WORKDIR /w
DF
  docker build -q -t mtcmp-py -f /tmp/Dockerfile.mtcmp /tmp > /dev/null 2>&1; echo "built mtcmp-py: $(docker image inspect mtcmp-py --format '{{.Size}}' 2>/dev/null) bytes"
fi
docker volume create mtcmp_hf >/dev/null
for cand in nllb-1.3b madlad-3b sarvam-4b; do
  echo "--- $cand"
  timeout 3000 docker run --rm -e MT_THREADS="${MT_THREADS:-8}" -e HF_HOME=/hf -v mtcmp_hf:/hf -v "$HOME/mt_compare.py:/w/mt_compare.py:ro" -v "$HOME/mt_sentences.json:/w/s.json:ro" -v "$OUT:/out" mtcmp-py python /w/mt_compare.py "$cand" /w/s.json "/out/$cand.json" </dev/null 2>&1 | grep -v -i "warning\|deprecat\|^$" | tail -18 | cut -c1-160
done
echo "--- cleanup"
docker volume rm mtcmp_hf >/dev/null 2>&1; cd ~/veerendra && git worktree remove --force ~/wt-mtcmp; git worktree prune
echo "left over: $(docker ps -a --format '{{.Names}}' | grep -c '^mtcmp-') containers, $(docker volume ls -q | grep -c '^mtcmp') volumes"
ls -la "$OUT" | tail -8
echo MTCMP_DONE
