# clients/elder-app/

**Owner:** Student 1 (Product & elder experience)

Reflex-based installable PWA — the elder client. Voice-first, large
touch targets, two-taps-to-anything, local-language UI with audio labels.

Status: bilingual (Telugu/English) home screen and chat screen live,
wired to the real gateway (`POST /session` + `ws://.../ws`) with genuine
send/receive against the shared circle, verified end-to-end with two
concurrent real sessions (persistence across reload + live push to an
already-open tab, no refresh needed). Voice-note recording works
(real `MediaRecorder` capture + local playback) but doesn't send
anywhere yet -- no backend endpoint for voice notes exists. See
`elder_app/elder_app.py`'s module docstring for the shared-circle vs.
per-contact-thread scope note, and `elder_app/gateway_ws_proof.py` for
the original connect/reconnect-with-backoff reference this was built on.
