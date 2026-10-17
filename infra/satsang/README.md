# infra/satsang — the broadcast media server
nginx + the RTMP module. A speaker publishes `rtmp://host:1935/live/<session id>?key=<key>`; the gateway
(`/sessions/ingest/*`) decides; nginx cuts HLS into tmpfs; Caddy serves `/hls/<session id>.m3u8`.
Start: `docker-compose.satsang.yml` (header has the command). Needs `SATSANG_HOOK_SECRET`, `SATSANG_PUBLIC_HOST`.
Defaults: 4 s fragments, 32 s playlist (about 15 s behind live), a silent publisher dropped after 30 s.
Test the entrypoint: `infra/satsang/test_entrypoint.sh`. Test the route: `infra/caddy/test_tls_internal.sh`.
Not here: RTMPS, recording, any login on `/hls`, a player. RTMP carries the key in clear (OPEN_QUESTIONS #50).
Rebuilding pulls whatever nginx version Debian has; re-run a real publish after one.
Never log at `info`: nginx-rtmp then writes the stream key.
Glass to glass: **predicted about 15 s; never measured on a phone or by a person** (15.2 s with ffmpeg as the listener, one run); fits only a LAN speaker and listener, 4 s fragments, hls.js default start. Step 9 of REHEARSAL.md (PR #175) measures it. Verdict: GATE_WEEK10.md.
