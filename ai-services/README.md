# ai-services/

**Owners:** Student 3 (Speech & language AI), Student 4 (Stewardship)

Speech recognition (ASR), machine translation (MT), speech synthesis (TTS),
and content moderation, each as separate FastAPI services on the GPU.
Kept out of the Reflex clients and gateway — the gateway calls these,
they don't talk to clients directly.

Status: skeleton stub only. `main.py` exposes `GET /health` on port 8001,
routed through Caddy at `/ai/*`. No ASR/MT/TTS/moderation models yet —
those land as their own services once Student 3/4 work starts.
