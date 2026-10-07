# The Week 8 gate, walked clause by clause (2026-10-06)

> "on staging, an onboarded elder records a Telugu voice note; a Hindi-preference receiver gets
> translated text plus natural audio with the original one tap away; the message passed a
> stewardship check whose decision is visible in the moderator console's audit log; circles and
> announcements run on the Postgres backbone; the measured p90 is written down, whatever it is."
> (docs/retro/month-1.md)

## What was run, and on what

- **Real staging** (`liveapp`, M1's folder, `596fa3f`): **read-only probes only.** Enabling the pipeline
  there means editing another member's folder and stack, so it was not touched.
- **The gate stack:** a throwaway compose project (`gate`) built from `team/main` (`596fa3f`), own
  volumes and ports. **It is staging-equivalent, not staging.** Real: gateway, ASR (faster-whisper
  `small`), MT, render (+Piper), Caddy with the real Caddyfile. Stub: moderation (keyword). The elder
  app was not built (its source was read, not run).
- The recording was made by **a real Chromium 152 (`MediaRecorder`, `audio/webm;codecs=opus`)**, but
  **not from a microphone**: on a plain-HTTP non-localhost origin the browser has no
  `navigator.mediaDevices` (and no `crypto.randomUUID`). The speech source is a **synthesized Piper
  Telugu sentence** whose true text is known: *"ఈ సాయంత్రం ఏడు గంటలకు ఆలయంలో భజన ఉంటుంది. అందరికీ స్వాగతం."*

## Verdicts

| Clause | Verdict | Evidence |
|---|---|---|
| an onboarded elder | **DOES NOT PASS on staging** | `/onboarding*` is not routed by Caddy: over HTTPS on staging `GET /onboarding/qr/junk` is **404** (HTML) and `POST /onboarding/activate` is **405**; the gateway itself answers the same call `400 malformed invite token` and `/moderation/queue` `401`. The elder app has **no call to `/onboarding`** (only comments saying QR onboarding is not merged). Onboarding works when the gateway is called directly: invite **201**, activate **200** (both token kinds), a second activate **410**. |
| records a Telugu voice note | **PASSES ONLY WITH A STOPGAP** | Genuine Chrome WebM/Opus (EBML `1a45dfa3`, 90,134 bytes), uploaded as `webm_opus`. **Stopgap off (the default): the job dies on attempt 1** (`no contracts.ai AudioFormat for chat format 'webm_opus'`), the message is `held`, `pipeline_state=failed`, a SYSTEM `HOLD` event, the Hindi receiver sees nothing, **the Telugu sender sees `held` and no notice** (#18). **Stopgap on: `sent`, `pipeline_state=complete`, job done on attempt 1.** Real staging today: `PIPELINE_ENABLED False`, `AI_ACCEPT_WEBM_AS_OGG_OPUS False`, AI = the mock; its database holds **0 moderation events, 0 renderings, 0 voice notes with a transcript, every message `pipeline_state` NULL**: it has never run the pipeline. |
| translated text plus natural audio | **PASSES ONLY ON A TECHNICALITY** | Hindi rendering with audio: `audio/wav`, 213,036 bytes, 4.83 s, **RMS 5990 (not silence)**. "Natural" is a human listening judgement that was **not made**. The text is wrong: see the finding below. |
| the original one tap away | **Contract and gateway: yes. M1's client: NOT VERIFIED** | The receiver's view carries `media_ref` and `transcript`; the original WebM is fetched by the receiver in **one request** (200, `audio/webm`, 90,134 bytes). `clients/elder-app` implements a "Show original" toggle (`recv_show_original`, `__satShowOriginal`, `elder_app.py` ~l.747–779); **that was read, not exercised.** A search of the deployed staging bundles found nothing either way (Reflex ships logic in server state), so it proves neither. |
| a stewardship check whose decision is visible in the moderator console's audit log | **DOES NOT PASS** | The decision is recorded and readable **through the gateway API as a moderator**: `classifier ALLOW, A_DEVOTIONAL, confidence 0.75, policy@2026-09-22, model stub-keywords@0, "[stub] No policy signal found; default devotional."` The elder gets **403**. But: **the check is the keyword stub**; **`/moderation*` was not routed** when this row was written (502 through the gate's Caddy, 404 on staging). **Update (phase 3a, PR open): it is routable now.** Before routing, the gateway was found to let a moderator's bare UUID read the queue and a held message's text in the default `AUTH_MODE=legacy`, so the moderator routes now accept ONLY a signed token in every mode; through a real Caddy, in both `legacy` and `jwt`: no token **401**, junk **401**, a bare UUID **401**, a signed non-moderator **403**, a signed moderator **200**. That reaches staging only when its Caddy restarts with the new Caddyfile and its gateway is rebuilt (M1's stack, not touched); and **the console (M4's `clients/admin-console/`) is in the repo but deployed nowhere**: it is in no compose file or Caddy route, and no console container runs on the server. **Correction (2026-10-07):** this row first said the console was a README only, which was true until PR #103 merged. **I did not run the console**, so "the decision is visible in the console's audit log" is **still NOT DEMONSTRATED**: routing is necessary, not sufficient, and does not upgrade this verdict; what is shown is the same trail through the gateway's API. The held note's queue item has `transcript: null` and `pivot_text_en: null`, so a moderator sees no machine-read text. |
| circles and announcements on the Postgres backbone | **PASSES** | An announcement circle created through Caddy; the admin posts (200); **a plain member's post is refused 403** ("Only a moderator or admin can post to an announcement circle"); the rows are in Postgres. Staging's own database (counts only): 33 circles (3 announcement), 47 memberships, 62 messages, schema `b9d4f1a27c3e`. |
| the measured p90 | **WRITTEN DOWN, WITH CAVEATS** | Below. |

## The finding on the gate itself: it passes while the transcript is in the wrong script

The 4 Oct Telugu note was transcribed into the wrong script and delivered with no signal. **That is
still true, and a gate that says "translated text plus natural audio" passes over it.**

- Today's walk: the transcript labelled `te` is **29 of 29 letters Devanagari, 0% Telugu**
  (`इसायंद्रम एदू गंतलकू आलेम लो बजनू उंदे अंदरी की स्वागतं`). Against the known sentence it looks like
  the spoken Telugu words **written in Devanagari** (a native reader should confirm), and the
  Hindi receiver is handed fluent-looking Hindi nonsense (`इसायंद्रम किसी भी गिद्ध का बजनू है …`).
  The classifier allowed it, the status is `sent`, nothing is flagged.
- Over **53 delivered Telugu-note transcripts from the Week 8 load runs**: **37 (70%) had 0%
  Telugu-script letters** (30 Devanagari, 6 Latin, 1 Kannada), 2 were mixed, **14 (26%) were at least
  80% Telugu.** All were delivered.
- 30 s Telugu notes (below): **8 different transcripts of identical audio**, 84 to 339 characters,
  27–100% Telugu script.
- Caveat: synthetic Piper speech, one sentence per file, a few synthesized files.
- A cheap guard exists in the gateway's own lane (a transcript whose letters are mostly not in the
  claimed language's script is held, not delivered). **Not built; it needs a decision.**

## The p90

All of these are **pipeline time** (`UNDO_WINDOW_SECONDS=0`, as in the load runs, so they are
comparable): **an elder waits at least the 30 s undo window**, i.e. `max(30 s, pipeline time)`.
Stopgap on; `AI_TRANSCRIBE_TIMEOUT_S` 120 s; synthetic speech built from **6 different sentences** per
language by `gate/make_long_speech.py` (a first attempt that looped one sentence six times was
transcribed as ~one phrase, so its timing meant nothing; it was discarded). One note at a time.

| 30 s note | length | n | p50 | p90 | worst | transcript |
|---|---|---|---|---|---|---|
| Hindi | 31.7 s | 8 | 29.1 s | 29.4 s | 29.4 s | complete (358 chars), identical 8 of 8, 100% Devanagari |
| Telugu | 34.1 s | 8 | 85.0 s | 119.9 s | 119.9 s | **8 different transcripts**, 84–339 chars |

Per note, Telugu (s): 74.2, 79.4, 82.6, 85.0, 99.4, 118.0, 119.4, 119.9. Hindi: 28.2 to 29.4.
All 16 delivered on attempt 1; **no timeout, no retry, no lost lease** in the gateway log; 16
classifier `ALLOW`.

- **With 8 notes, p90 is the worst value.**
- **Three Telugu notes finished between 118 and 120 s, right under the 120 s ASR timeout.** None timed
  out, but there is no margin to see: a slightly slower decode becomes a timeout and a retry.
- Telugu time depends on how much the decoder emits (the 339-character runs are the slow ones); that
  is `services/ai/speech` (M3's).
- Not measured: real elder voices, a smaller or limited host (no CPU limit was set), concurrent
  traffic at 30 s (see "Ten notes at once" in `README.md`: the queue is serial, so the wait for the
  10th note is about ten times these).

## What this does not show

- A real microphone, a real elder, the real elder-app UI, real staging with the pipeline on.
- Whether the Hindi audio is "natural".
- That "Show original" works in M1's client.
- A real stewardship classifier: none has run.
- `/onboarding` and `/moderation` on **real staging** (both are routed in the Caddyfile by open PRs, but staging's Caddy has not been restarted with them).
- The moderator console itself (M4's, merged in #103: queue, side-by-side review, release/block and an audit trail, on fixtures by default, or the chat mock or the gateway by configuration): not run, not deployed.

## Re-running (the scripts in `e2e/gate/`)

They are the scripts **as run**; paths assume a worktree at `~/wt-gate` and this folder copied to
`~/gate-walk` on the server. `up.sh` (stack), `step1.sh` (seed, onboard, sample), `stage_wav.py` and
`record_in_browser.js` (the recording), `inspect.sh` and `walk.py` (what each reader and the moderator
see; the writing script of every text is counted), `circles.sh`, `stopgap.sh on|off`, `make_long_speech.py`
and `p90.sh` (needs `real_driver.py burst` and `load_stats.py` from PR #110).
