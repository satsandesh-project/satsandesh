#!/usr/bin/env bash
# Real test of the opt-in HTTPS mode (docker-compose.tls.yml): the REAL Caddy binary, the REAL
# Caddyfile, the REAL entrypoint script, three fake upstreams that answer with their own name.
# Run on any host with docker:   infra/caddy/test_tls_internal.sh
# It binds no host ports and touches no running stack (own network, own containers).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
N=tlstest$$
SUBNET=172.29.77.0/24
CADDY_IP=172.29.77.10
cleanup() { docker rm -f ${N}-gw ${N}-el ${N}-ai ${N}-caddy >/dev/null 2>&1; docker network rm "$N" >/dev/null 2>&1; rm -f "$ROOT"; }
ROOT="$(mktemp)"
trap cleanup EXIT

[ -f "$HERE/tls-internal-entrypoint.sh" ] || { echo "FAIL  infra/caddy/tls-internal-entrypoint.sh does not exist"; exit 1; }
docker network create --subnet "$SUBNET" "$N" >/dev/null || { echo "FAIL  could not create test network $SUBNET"; exit 1; }

fake() {  # fake <alias> <port> <label> <suffix>
  docker run -d --name "${N}-$4" --network "$N" --network-alias "$1" python:3.11-slim python -c "
import http.server as h
class H(h.BaseHTTPRequestHandler):
    def do_GET(self):
        b = ('$3 ' + self.path).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
h.HTTPServer(('0.0.0.0', $2), h.BaseHTTPRequestHandler if False else H).serve_forever()" >/dev/null
}
fake gateway 8000 GATEWAY gw
fake elder-app 3000 ELDER-APP el
fake ai-services 8001 AI-STUB ai

docker run -d --name "${N}-caddy" --network "$N" --ip "$CADDY_IP" --network-alias caddy \
  -e TLS_HOST="$CADDY_IP" \
  -v "$HERE/Caddyfile":/etc/caddy/Caddyfile:ro \
  -v "$HERE/tls-internal-entrypoint.sh":/tls-internal-entrypoint.sh:ro \
  --entrypoint /bin/sh caddy:2-alpine /tls-internal-entrypoint.sh >/dev/null

echo "waiting for Caddy to create its internal root..."
for _ in $(seq 1 40); do
  docker cp "${N}-caddy:/data/caddy/pki/authorities/local/root.crt" "$ROOT" >/dev/null 2>&1 && [ -s "$ROOT" ] && break
  sleep 1
done
[ -s "$ROOT" ] || { echo "FAIL  Caddy never produced a root certificate; its log:"; docker logs "${N}-caddy" 2>&1 | tail -15; exit 1; }
sleep 2

docker run --rm --network "$N" -v "$HERE/tls_check.py":/c.py:ro -v "$ROOT":/root.crt:ro \
  python:3.11-slim python /c.py "$CADDY_IP" /root.crt
