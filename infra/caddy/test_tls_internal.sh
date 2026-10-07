#!/usr/bin/env bash
# Real test of the opt-in HTTPS mode (docker-compose.tls.yml): the REAL Caddy binary, the REAL
# Caddyfile, the REAL entrypoint script, three fake upstreams that answer with their own name.
# Run on any host with docker:   infra/caddy/test_tls_internal.sh
# It binds no host ports and touches no running stack (own network, own containers).
#
# Three cases, because each catches something the others cannot:
#   - an IP address as TLS_HOST   (Caddy would pick its internal CA for a bare IP by itself)
#   - a NAME as TLS_HOST          (without `tls internal` Caddy would try a PUBLIC certificate)
#   - a Caddyfile with no ':80 {' (the entrypoint must refuse to guess, exit 3)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
N=tlstest$$
SUBNET=172.29.77.0/24
CADDY_IP=172.29.77.10
ROOT="$(mktemp)"
BAD="$(mktemp)"
cleanup() { docker rm -f ${N}-gw ${N}-el ${N}-ai ${N}-caddy ${N}-caddy2 >/dev/null 2>&1; docker network rm "$N" >/dev/null 2>&1; rm -f "$ROOT" "$BAD"; }
trap cleanup EXIT
status=0

[ -f "$HERE/tls-internal-entrypoint.sh" ] || { echo "FAIL  infra/caddy/tls-internal-entrypoint.sh does not exist"; exit 1; }
docker network create --subnet "$SUBNET" "$N" >/dev/null || { echo "FAIL  could not create test network $SUBNET"; exit 1; }

fake() {  # fake <alias> <port> <label> <suffix>
  docker run -d --name "${N}-$4" --network "$N" --network-alias "$1" python:3.11-slim python -c "
import http.server as h
class H(h.BaseHTTPRequestHandler):
    def do_GET(self):
        b = ('$3 ' + self.path).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    do_POST = do_GET
    def log_message(self, *a): pass
h.HTTPServer(('0.0.0.0', $2), H).serve_forever()" >/dev/null
}
fake gateway 8000 GATEWAY gw
fake elder-app 3000 ELDER-APP el
fake ai-services 8001 AI-STUB ai

run_case() {  # run_case <label> <TLS_HOST> [extra docker args...]
  local label=$1 host=$2; shift 2
  echo; echo "=== case: $label (TLS_HOST=$host)"
  docker rm -f ${N}-caddy >/dev/null 2>&1; : > "$ROOT"
  docker run -d --name "${N}-caddy" --network "$N" "$@" \
    -e TLS_HOST="$host" -e TLS_PUBLIC_PORT=8443 \
    -v "$HERE/Caddyfile":/etc/caddy/Caddyfile:ro \
    -v "$HERE/tls-internal-entrypoint.sh":/tls-internal-entrypoint.sh:ro \
    --entrypoint /bin/sh caddy:2-alpine /tls-internal-entrypoint.sh >/dev/null
  for _ in $(seq 1 40); do
    docker cp "${N}-caddy:/data/caddy/pki/authorities/local/root.crt" "$ROOT" >/dev/null 2>&1 && [ -s "$ROOT" ] && break
    sleep 1
  done
  [ -s "$ROOT" ] || { echo "FAIL  Caddy never produced a root certificate; its log:"; docker logs "${N}-caddy" 2>&1 | tail -8; status=1; return; }
  sleep 3
  docker run --rm --network "$N" -e EXPECT_TLS_PORT=8443 -v "$HERE/tls_check.py":/c.py:ro -v "$ROOT":/root.crt:ro \
    python:3.11-slim python /c.py "$host" /root.crt || status=1
  nosni_check "$host"
}

# The certificate name is NOT the address the container is reached on (as with docker port
# publishing: the browser types the host's address, Caddy sees the container's), and the client
# sends no SNI because it connected to an IP. Caddy must still know which certificate to serve.
nosni_check() {  # nosni_check <expected certificate name>
  local cip; cip=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$N\").IPAddress}}" "${N}-caddy")
  docker run --rm --network "$N" -v "$HERE/tls_nosni.py":/n.py:ro -v "$ROOT":/root.crt:ro \
    python:3.11-slim python /n.py "$cip" "$1" /root.crt || status=1
}

run_case "an IP address" "$CADDY_IP" --ip "$CADDY_IP" --network-alias caddy
run_case "a hostname" "staging.test" --network-alias staging.test

echo; echo "=== case: a certificate name that is not the container's own address (docker port publishing)"
docker rm -f ${N}-caddy >/dev/null 2>&1; : > "$ROOT"
docker run -d --name "${N}-caddy" --network "$N" -e TLS_HOST=10.20.30.40 -e TLS_PUBLIC_PORT=8443 \
  -v "$HERE/Caddyfile":/etc/caddy/Caddyfile:ro -v "$HERE/tls-internal-entrypoint.sh":/tls-internal-entrypoint.sh:ro \
  --entrypoint /bin/sh caddy:2-alpine /tls-internal-entrypoint.sh >/dev/null
for _ in $(seq 1 40); do
  docker cp "${N}-caddy:/data/caddy/pki/authorities/local/root.crt" "$ROOT" >/dev/null 2>&1 && [ -s "$ROOT" ] && break
  sleep 1
done
[ -s "$ROOT" ] || { echo "FAIL  Caddy never produced a root certificate"; status=1; }
sleep 3
nosni_check "10.20.30.40"

echo; echo "=== case: a Caddyfile with no ':80 {' line must be refused, not guessed"
printf 'example.com {\n\trespond "x"\n}\n' > "$BAD"
out=$(docker run --rm -e TLS_HOST=x -e CADDYFILE_SRC=/bad -v "$BAD":/bad:ro \
  -v "$HERE/tls-internal-entrypoint.sh":/e.sh:ro --entrypoint /bin/sh caddy:2-alpine /e.sh 2>&1)
rc=$?
if [ "$rc" = 3 ] && echo "$out" | grep -q "refusing to guess"; then
  echo "PASS  exit 3 with a clear message"
else
  echo "FAIL  expected exit 3 and 'refusing to guess', got exit $rc: $out"; status=1
fi

echo; [ $status = 0 ] && echo "ALL PASSED" || echo "SOME FAILED"
exit $status
