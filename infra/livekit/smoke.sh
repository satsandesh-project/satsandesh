#!/usr/bin/env bash
# Starts LiveKit alone from docker-compose.livekit.yml (project lk-smoke, throwaway) and runs
# smoke_check.py against it with the gateway's own code. Run from the repo root on a host with
# Docker. Placeholder values only; nothing here touches a real stack.
set -uo pipefail
cd "$(dirname "$0")/../.."
P=lk-smoke
export LIVEKIT_API_KEY=smokekey
export LIVEKIT_API_SECRET=smoke-secret-smoke-secret-smoke-secret-01
export LIVEKIT_NODE_IP=127.0.0.1
export LIVEKIT_PUBLIC_URL=wss://smoke.test/livekit
# the base file requires these to parse; none of those services is started
export GATEWAY_JWT_SECRET=x POSTGRES_PASSWORD=x GATEWAY_VAPID_PRIVATE_KEY=x GATEWAY_VAPID_PUBLIC_KEY=x
export POSTGRES_USER=x POSTGRES_DB=x DATABASE_URL=postgresql://x:x@x/x SESSION_SECRET=x

cleanup() { docker compose -p $P -f docker-compose.yml -f docker-compose.livekit.yml down -v >/dev/null 2>&1; }
trap cleanup EXIT

docker compose -p $P -f docker-compose.yml -f docker-compose.livekit.yml up -d livekit \
  || { echo "FAIL  could not start livekit"; exit 1; }
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "${P}-livekit-1" 2>/dev/null)" = healthy ] && break
  sleep 2
done
[ "$(docker inspect -f '{{.State.Health.Status}}' "${P}-livekit-1")" = healthy ] \
  || { echo "FAIL  livekit never became healthy; its log:"; docker logs "${P}-livekit-1" 2>&1 | tail -15; exit 1; }
echo "PASS  livekit started from docker-compose.livekit.yml and is healthy"

docker run --rm --network "${P}_default" \
  -v "$PWD":/src -w /src/services/gateway \
  -e LIVEKIT_API_KEY -e LIVEKIT_API_SECRET -e LIVEKIT_PUBLIC_URL -e LIVEKIT_INTERNAL_URL=http://livekit:7880 \
  -e DATABASE_URL=postgresql://x:x@x/x -e JWT_SECRET=x -e VAPID_PRIVATE_KEY=x -e VAPID_PUBLIC_KEY=x \
  -e VAPID_SUBJECT=mailto:x@example.com -e MEDIA_STORAGE_ROOT=/tmp/m -e PYTHONPATH=/src/services/gateway:/src \
  python:3.11-slim sh -c "pip install -q -r requirements.txt && python /src/infra/livekit/smoke_check.py"
