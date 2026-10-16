# The Week 10 gate, walked clause by clause (2026-10-16)

> "a satsang streams from a phone"; "an elder gets the reminder"; "and listens live."
> (M2 Feature 1, Weeks 9 and 10, as the task states it; the three clauses are walked separately.)

The standard is `infra/ai/GATE_WEEK8.md`: each clause gets PASS, PASS WITH A QUALIFIER or FAIL, with the evidence and
the date, and a green test run is not evidence for a clause about people and phones.

## What was run, and on what

- **Real staging** (`liveapp`, M1's folder, `~/kshitiz/live-app`): **read-only probes only**, 2026-10-16 15:53 UTC.
  Nothing of Week 9 or 10 is deployed there (below). Changing it needs M1's written go; the plan is `DEPLOY.md` (#176).
- **Throwaway stacks** (a separate compose project, own ports, removed afterwards), built from the Week 9 and 10
  branches: the **real** gateway, nginx-rtmp media server, Postgres, the AI services for the reminder's text and audio.
  **They are staging-equivalent, not staging.**
- **Speaker:** `ffmpeg` publishing a synthesized tone over RTMP. **Not a phone, not Larix, not OBS.**
- **Listener:** the real `satsang_player.js` with the real hls.js 1.5.17 in **headless Chrome 155 on a laptop**,
  driven over the DevTools protocol. No human ear, no phone, no speaker output.

## Verdicts

| Clause | Verdict | Evidence |
|---|---|---|
| a satsang streams from a phone | **FAIL** | **On staging:** no `sessions` table (alembic head `a7c2e91f4b36`; the satsang tables arrive with `d3a9f1c27b58` and its two successors), no media server (nothing listens on 1935), no gateway route (`/sessions` and `/hls/x.m3u8` answer the elder app's HTML 404, 5,171 bytes, over HTTPS on 8443), the running Caddy holds no route for either. **Elsewhere:** a real RTMP publish by `ffmpeg` to the real media server on a throwaway stack goes live through the gateway's hook, is cut into HLS, and is heard (below). **No phone has ever published**: Larix and OBS were never installed, and the one hard fact about phones is that campus Wi-Fi lets 1935/TCP through from a laptop (`INGEST_FINDING.md`). `REHEARSAL.md` steps 2 and 6 (Larix, then going live) are the check that would change this verdict and has **not been run**. |
| an elder gets the reminder | **FAIL** | **On staging:** nothing is deployed, `push_subscriptions` has **0** rows. **Anywhere:** the reminder job fires 30 minutes before the start, renders Telugu, Hindi and English text and audio (a real stack, 14 Oct: speech audio RMS 3694 and 5492, i.e. not silence), and sends a web push per subscriber (the per-recipient ledger that makes a crash cost one duplicate, never a missing reminder is #177, not merged). FCM accepted six test sends (HTTP 201, 299 to 904 ms). **But it reaches no phone:** the elder app has no service worker and no subscribe call, Caddy leaves `/push*` unrouted, and nothing plays the reminder's audio (OPEN_QUESTIONS #51). No browser push event was observed in 120 s. **No human heard the reminder, in any language**; "natural" and the Telugu wording are judgements nobody has made. |
| and listens live | **FAIL on staging; PASS WITH A QUALIFIER on the throwaway stack** | **On staging:** no Satsang screen (`/satsang` answers the HTML 404), nothing to listen to. **Throwaway stack:** the real player and hls.js decoded an audio-only stream to a measured RMS of **0.247 against 0.2475 expected**; with the video track nginx-rtmp declares, 4 of 5 runs played and 1 paused at 12.7 s **unexplained**. The qualifier is large: a laptop's headless Chrome, **no phone, no human ear**. A real segment declares H.264 in its program table with **zero video packets**, so how a phone's browser treats it is untested (#39). When a satsang ends the player stalls and reports a fatal error but stays on screen; a clean end cuts off the last 10 to 15 s (#52). |
| **The week's clause as a whole** | **FAIL, 2026-10-16** | Every part exists as code, tested on a throwaway stack with stand-ins for the phone and the person. **None of it runs on staging and none of it has been used by a person.** |

## What would turn each FAIL around

1. **Streams from a phone:** M1's go; `DEPLOY.md` (#176); then `REHEARSAL.md` steps 2 and 6 on a real phone (`REHEARSAL.md` is in #175). One person, one phone, about 10 minutes.
2. **Gets the reminder:** a service worker and a subscribe call in the elder app and a Caddy route for `/push*` (#51, M1's files), then step 5; and a native speaker reading the Telugu text.
3. **Listens live:** the same deploy, then steps 7 to 9 on at least two phones; step 10 for the drop.

## The glass-to-glass number

**Not measured on a phone, or by a person.** Predicted **about 15 s**; `infra/satsang/README.md` carries it with the
preconditions. The only measurements are on a throwaway stack: **15.2 s** with `ffmpeg` as the listener (Week 9, PR #165,
one real publish); and, with the real player in headless Chrome, a publisher that stopped dead was still heard for
about 10 to 12 s (16 Oct: a clean end and the 15 s and 25 s pauses of #43), which agrees with the prediction and is a buffer reading, not a latency.

## Not done, and where it goes

- Nothing was deployed. Deploy plan: `DEPLOY.md`; it needs M1's written go and, since #177, covers three migrations, not two.
- The speaker-drop grace period was priced and deferred to Week 16 (OPEN_QUESTIONS #43, PR #178); its workaround is in `REHEARSAL.md`.
- Merges outstanding at this date: #170, #171, #172, #175, #176, #177, #178. Until they merge, `main` holds the satsang
  model, API, admin page and ingest (#173), and **not** the reminder's delivery, the elder screens or the rehearsal.
