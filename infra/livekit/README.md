# infra/livekit/

LiveKit carries the audio of bhajan rooms; the gateway (`services/gateway/app/rooms.py`) decides who may speak.

1. Generate a key pair: `docker run --rm livekit/livekit-server:v1.13.9 generate-keys`. Put `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_NODE_IP` (the host address phones reach) and `LIVEKIT_PUBLIC_URL` (`wss://<host>:<port>/livekit`) in `.env`. Never commit the secret.
2. Add `-f docker-compose.livekit.yml` to the compose command the stack was started with, then `up -d livekit gateway`. Caddy must be restarted once to pick up the `/livekit` route.
3. Open 7881/tcp and 7882/udp to phones. A network that blocks both needs TURN over TLS on 443, which needs a certificate for a real name; it is not configured here.
4. `infra/livekit/smoke.sh` starts LiveKit alone and checks the gateway's tokens and admin calls against it.
