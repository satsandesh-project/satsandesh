# infra/satsang — the broadcast media server
nginx + the RTMP module. A speaker publishes `rtmp://host:1935/live/<session id>?key=<key>`; the gateway
(`/sessions/ingest/*`) decides; nginx cuts HLS into tmpfs; Caddy serves `/hls/<session id>.m3u8`.
Start: `docker-compose.satsang.yml` (header has the command). Needs `SATSANG_HOOK_SECRET`, `SATSANG_PUBLIC_HOST`.
Defaults: 4 s fragments, 32 s playlist (about 15 s behind live), a silent publisher dropped after 30 s.
Test the entrypoint: `infra/satsang/test_entrypoint.sh`. Test the route: `infra/caddy/test_tls_internal.sh`.
Not here: RTMPS, recording, any login on `/hls`, a player. RTMP carries the key in clear (OPEN_QUESTIONS #36).
Rebuilding pulls whatever nginx version Debian has; re-run a real publish after one.
Never log at `info`: nginx-rtmp then writes the stream key.
Measured numbers and what was NOT measured: the Phase 3 PR description.
