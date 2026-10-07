#!/usr/bin/env bash
# Seeds the gate stack (family member + moderator rows), onboards the elder and the receiver through the
# real /onboarding routes, makes the Telugu speech sample, and serves it to the browser with CORS.
set -uo pipefail
cd ~/wt-gate || exit 1
DC="docker compose -p gate -f docker-compose.yml -f docker-compose.ai.yml"
PGUSER=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2); PGDB=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2)
psql_q() { $DC exec -T postgres psql -q -t -A -U "$PGUSER" -d "$PGDB" -c "$1"; }
mkdir -p ~/gate-state ~/gate-samples
psql_q "INSERT INTO users (id,name,preferred_language,role) VALUES
  ('00000000-0000-4000-8000-0000000000f1','Family member','en','elder'),
  ('00000000-0000-4000-8000-0000000000a1','Moderator','en','moderator')
  ON CONFLICT (id) DO NOTHING" >/dev/null
echo "=== A. onboarding (direct to the gateway, then through Caddy)"
docker run --rm --network gate_default -v ~/wt-gate/infra:/infra -v ~/gate-state:/state -v ~/gate-walk:/walk gate-gateway python /walk/walk.py onboard
echo "=== the Telugu speech sample (Piper te_IN-maya, from the render service: synthetic, like the 4 Oct proof)"
JSON=$($DC exec -T render python - < infra/ai/e2e/real_make_speech.py)
echo "$JSON"
TE_WAV=$(echo "$JSON" | sed -n 's/.*"te": "\([^"]*\)".*/\1/p')
RID=$($DC ps -q render)
docker cp "$RID:/render-output/$TE_WAV" ~/gate-samples/te.wav && ls -l ~/gate-samples/te.wav
cat > ~/gate-samples/serve.py <<'EOF'
import http.server, functools
class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*"); super().end_headers()
http.server.ThreadingHTTPServer(("0.0.0.0", 18501), functools.partial(H, directory="/home/satsandesh/gate-samples")).serve_forever()
EOF
(nohup python3 ~/gate-samples/serve.py > ~/gate-samples/serve.log 2>&1 < /dev/null &)
sleep 2; curl -s -o /dev/null -w "sample served: %{http_code} %{size_download} bytes\n" http://127.0.0.1:18501/te.wav
cat ~/gate-state/gate_state.json | python3 -c "import sys,json; d=json.load(sys.stdin); print('STATE', {k:{'user_id':v['user_id']} for k,v in d.items()})"
echo GATE_STEP1_DONE
