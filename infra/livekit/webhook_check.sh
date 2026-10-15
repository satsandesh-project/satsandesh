#!/bin/bash
# Does a real LiveKit send `participant_joined` to the gateway's webhook in the shape and with the
# signature the gateway expects (services/gateway/app/livekit_webhook.py)? Starts a throwaway LiveKit
# (no host ports) and one container that joins it with LiveKit's Python SDK. Needs docker; the SDK
# is pip-installed inside that container only. Run from anywhere:  bash infra/livekit/webhook_check.sh
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
N=lkhook$$
KEY=devkey
SECRET=a-livekit-api-secret-of-at-least-thirty-two-bytes
cleanup() { docker rm -f ${N}-lk ${N}-v >/dev/null 2>&1; docker network rm $N >/dev/null 2>&1; }
trap cleanup EXIT
docker network create $N >/dev/null
docker run -d --name ${N}-lk --network $N \
  -e LIVEKIT_KEYS="$KEY: $SECRET" \
  -e LIVEKIT_CONFIG="port: 7880
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false
room:
  auto_create: false
webhook:
  api_key: $KEY
  urls:
    - http://${N}-v:9000/hook
" livekit/livekit-server:v1.13.9 >/dev/null
sleep 4
docker run --rm --name ${N}-v --network $N -v "$ROOT":/src:ro -v "$ROOT/infra/livekit/webhook_check.py":/check.py:ro \
  -e LIVEKIT_INTERNAL_URL=http://${N}-lk:7880 -e LIVEKIT_API_KEY=$KEY -e LIVEKIT_API_SECRET=$SECRET \
  -e LIVEKIT_PUBLIC_URL=wss://x/livekit -e DATABASE_URL=postgresql://u:p@nowhere/db -e JWT_SECRET=ci-placeholder-secret \
  -e VAPID_PRIVATE_KEY=x -e VAPID_PUBLIC_KEY=y -e VAPID_SUBJECT=mailto:ci@example.com -e MEDIA_STORAGE_ROOT=/tmp/m \
  -e PYTHONPATH=/src/services/gateway:/src python:3.11 \
  bash -c "pip install -q --no-cache-dir -r /src/services/gateway/requirements.txt livekit >/dev/null 2>&1; python /check.py"
