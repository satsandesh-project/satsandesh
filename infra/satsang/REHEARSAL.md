# Satsang rehearsal: a checklist, run in one sitting

**STATUS: UNRUN.** Nobody has done any step in "The checklist". Everything under "Checked before" was run from a
laptop or the shared server with ffmpeg and a headless Chrome. **None of it involved a phone, a human ear, M1's stack
or the real gateway.** Prepared 2026-10-16, branch `feat/m2-w10-rehearsal-prep`.

Each step is: **Do**, **Expect**, **Predicted** (written before the run, with the reason: a wrong prediction is the
useful result), and **Actual** (you fill it in). Times are wall-clock, written down as you see them.

Run on ____ by ____ . Phones: ____ (model, OS, browser) . Campus Wi-Fi SSID: ____

## Read first: what the preparation found

1. **The reminder push cannot reach any phone today.** The elder app has no service worker and never calls
   `POST /push/subscribe`, and `infra/caddy/Caddyfile` leaves `/push*` unrouted on purpose (its own comment says so).
   The only subscriber is the Week 3 manual page (`services/gateway/manual_verification/`). Step 5 is built around that.
2. **Nothing plays the spoken reminder.** The audio sits behind `GET /sessions/{id}/reminder/audio`, which needs an
   `Authorization` header; no screen asks for it. A human can hear it only through `curl` (step 5).
3. **The listener's screen probably cuts off the end of the discourse.** The list says "ended" within 12 s of the speaker
   stopping, and the screen then stops the player; the listener is 10 to 15 s behind live. Step 8 is written to catch it.
4. **A stream WITH video behaves differently from the audio-only stream that was tested** (OBS cannot send audio only;
   a phone app might not either). It played in 4 of 5 headless runs and paused once, unexplained. Step 3 and 7.

## Checked before (from this machine; each line says how)

| What | How | Result |
|---|---|---|
| The empty video stream of #39 | `ffprobe` 4.2.7 on the shared server, then a raw MPEG-TS packet count on a laptop, of 5 segments the real media server (nginx 1.26.3, rtmp module 1.2.2, built from `infra/satsang`) cut from an audio-only AAC publish by **ffmpeg, not a phone**; the gateway's hooks were a stub that answered 200 | The PMT lists **H.264 (stream_type 0x1B) on PID 0x100** and AAC-LC 44.1 kHz mono on PID 0x101. ffprobe: `Video: h264, none, 90k tbr`, width 0, height 0, no profile. The PMT's `PCR_PID` is 0x100, and **in all 5 segments there are 0 TS packets on PID 0x100 and no PCR at all** |
| Does hls.js accept it? | Installed **headless Chrome 155** on Windows, driven over the DevTools protocol (no Playwright, nothing downloaded), loading the vendored `hls.min.js` 1.5.17 and the real `satsang_player.js`; the stream was reached through an SSH tunnel and a same-origin proxy (not through Caddy), 2 runs of 50 s and 150 s | Yes. hls.js creates **one audio SourceBuffer and no video** (`BUFFER_CREATED tracks: audio`), buffers a segment every 4 s, `playing` fired 165 ms after `play()`. The **decoded output measured RMS 0.247 against 0.2475 expected** for a 0.35-amplitude sine (silence reads 0). No hls.js error before the stream ended |
| A stream with a real video track | Same page, a 160x120 5 fps H.264 + AAC publish by ffmpeg (what OBS would send), 5 valid runs (a sixth used a session id nobody had published, and is not counted) | `BUFFER_CREATED tracks: audio,video`. **4 runs played cleanly** (one of them logged two non-fatal `bufferStalledError` in the first 3 s); **1 run paused by itself at 12.7 s** (`pause` event, no `waiting`, no hls.js error, player status stayed `playing`), **cause unknown**, not reproduced in the 4 later runs |
| A publisher that stops cleanly | Same page, ffmpeg publishing 70 s then closing, one run | The `done` hook was posted at the disconnect. Audio ran out about **10 s after the publish ended** (the listener joined at the start, so that is a floor, not the usual 12 to 16 s). The playlist kept answering **200 (frozen) for about 55 s after the audio ran out, then 404**. hls.js raised a **fatal** `levelLoadError` 15 s after the first 404; the player stayed `stalled` and never `failed`, as designed |
| The compose layering of step 1 | `docker compose config --quiet` on the server with a checkout of the elder-screens branch, nothing started | It parses; services: `render satsang speech ai-services postgres gateway elder-app caddy moderation mt`; host ports 8444, 18470, 55470 and 1935 were free at that moment |
| Not run at all | | Larix, OBS, any phone, Safari/iOS, Android, 3G, the real gateway's hooks with these segments, Caddy in front of the stream, the reminder on a phone, the Telugu and Hindi audio by a human ear |

The tools are `infra/satsang/rehearsal/listen_check_*` (see step 6).

## Before you start

- [ ] #173, #170, #171, #172 merged, so `main` has `infra/satsang/`, the routes, the reminder and the elder screens. ____
- [ ] M1 told that the rehearsal stack copies (read-only) the model volume `liveapp_ai_models` (2.5 GB), and that nothing
      is changed on `liveapp`. He does not need to restart anything for this stack. ____
- [ ] A phone with the **internal CA installed and trusted**, and a second without it (to see the certificate case). ____
- [ ] Larix Broadcaster installed on the speaker's phone; OBS on a laptop; a laptop with Chrome and Node 22+. ____
- [ ] A clock everybody can see (a phone's stopwatch app or `time.is`). ____

## The checklist

### 1. The stack: which, started how, on which host

- **Do**, on 10.110.11.31 as `satsandesh`, a **separate project named `rehearsal`**, never `liveapp`:
  ```bash
  cd ~/veerendra && git fetch team && git worktree add ~/wt-rehearsal team/main && cd ~/wt-rehearsal
  cp ~/veerendra/.env .env
  export TLS_HOST=10.110.11.31 CADDY_TLS_HOST_PORT=8444 CADDY_HOST_PORT=18470 POSTGRES_HOST_PORT=55470 \
         SATSANG_PUBLIC_HOST=10.110.11.31 SATSANG_RTMP_HOST_PORT=1935 PUBLIC_ORIGIN=https://10.110.11.31:8444 \
         SATSANG_HOOK_SECRET=$(head -c 40 /dev/urandom | base64 | tr -dc A-Za-z0-9 | head -c 24)
  DC="docker compose -p rehearsal -f docker-compose.yml -f docker-compose.ai.yml -f docker-compose.tls.yml -f docker-compose.satsang.yml"
  docker volume create rehearsal_ai_models      # compose may warn it did not create it: harmless
  docker run --rm -v liveapp_ai_models:/from:ro -v rehearsal_ai_models:/to alpine sh -c 'cp -a /from/. /to/'
  $DC up -d --build postgres gateway elder-app caddy satsang render mt
  ```
- **Expect:** `$DC ps` shows postgres, gateway, elder-app, caddy, satsang, render and mt `healthy`.
- **Predicted:** the first build takes a long time (**not timed by me**) and render and mt are the ones that fail: they need
  the gated models and `HF_TOKEN`; the copy above is meant to cover the models, and whether the token is in `.env` is
  **unchecked**. If render is not healthy, step 5 cannot produce Telugu or Hindi, and everything else still works.
- **Actual:** ____

Then make the people (SQL copied from my Phase 1 and 2 scripts, which ran on a different throwaway stack):
```bash
PGC=$($DC ps -q postgres </dev/null)
psqlq() { printf '%s\n' "$1" | docker exec -i "$PGC" sh -c 'psql -P pager=off -q -t -A -F"|" -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; }
mk() { psqlq "insert into users (id, name, preferred_language, role, tts_on) values (gen_random_uuid(), '$1', '$2', '$3', true) returning id" | head -1; }
ADMIN=$(mk 'Rehearsal admin' en admin);  SPEAKER=$(mk 'Rehearsal speaker' te elder)
TE=$(mk 'Rehearsal te' te elder);        HI=$(mk 'Rehearsal hi' hi elder);  EN=$(mk 'Rehearsal en' en elder)
ADMIN_TOKEN=$($DC exec -T gateway python -m app.tokens "$ADMIN" </dev/null | tail -1)
echo "speaker id (type it into the admin page): $SPEAKER"      # never paste ADMIN_TOKEN or a user id into a chat or an issue
```
In `AUTH_MODE=legacy` a user id **is** a bearer credential. The phones' own ids come from onboarding in the app (step 7).

### 2. The speaker's app: Larix Broadcaster

- **Do:** on the speaker's phone, publish to `rtmp://10.110.11.31:1935/live/<session id>?key=<key>`. Both come from the admin
  page at `https://10.110.11.31:8444/satsang-admin` (sign in by pasting `ADMIN_TOKEN`; scheduling in step 4 shows the whole
  URL once, masked, with Show and Copy; it wipes itself after 2 minutes; **a lost key is replaced with "New key", not
  recovered**). Set Larix to **audio only** if it can; if it cannot, note that and expect the "with video" row above.
  (I have not installed Larix: its menu names are not written here because I would be guessing.)
- **Expect:** Larix shows connected; nothing is refused.
- **Predicted:** it connects. The gateway's publish hook accepts a valid key (measured with ffmpeg on a real stack in Week 9),
  and `?key=` after the stream name is what ffmpeg sent. **Unknown:** whether Larix keeps the `?key=...` part when it splits
  the URL into host, application and name; if the connection is closed at once, that is the first suspect.
- **Actual:** ____

### 3. The fallback: OBS Studio, same URL

- **Do:** Settings, Stream, service **Custom**. Server `rtmp://10.110.11.31:1935/live`, Stream Key `<session id>?key=<key>`.
  OBS cannot send audio only, so make the video cheap: Video 160x120 at 5 fps, x264 `veryfast`, tune `zerolatency`,
  **keyframe interval 2 s**, bitrate 100 kbps; audio AAC 64 kbps mono.
- **Expect:** the same result as step 2, with real (tiny) video in the stream.
- **Predicted:** it connects and plays in a desktop browser (the "with video" row above: 4 of 5 runs), **but** the keyframe interval
  matters: nginx-rtmp cuts a fragment at a keyframe once 4 s have passed (its documented behaviour; **I did not measure it
  with a long interval**), so a 10 s keyframe interval means 10 s fragments and a delay that grows by about that much.
  Watch for the unexplained pause of the 1-in-4 run.
- **Actual:** ____

### 4. Schedule a satsang 31 minutes out

- **Do:** on the admin page, title `Rehearsal`, speaker id from step 1, start time **now + 31 minutes** (your phone's local time).
  Note the time you pressed Schedule: ____ . Then:
  ```bash
  psqlq "select status, to_char(next_attempt_at at time zone 'utc','HH24:MI:SS'), attempts from jobs where job_type='satsang_reminder'"
  ```
- **Expect:** a notice "Scheduled", the ingest URL panel, and exactly one `queued` reminder job due in about 1 minute.
- **Predicted:** exactly that, to the second: this was measured on a real clock in Week 10 Phase 1 (a job queued for 60 s, then
  `done` at its due time), on a throwaway stack with no push subscribers.
- **Actual:** ____

### 5. The reminder: which devices, which languages, text and audio

First, **seed three subscribers** whose pushes are meant to fail (the endpoints do not resolve), because with no subscriber
the handler renders nothing but the English source text (read from the code, `satsang_reminder.py`), so there would be no
Telugu, no Hindi and no audio to hear. Do this **before the reminder is due, within the first minute** after step 4:
```bash
sub() { psqlq "insert into push_subscriptions (id, user_id, endpoint, p256dh, auth) values (gen_random_uuid(), '$1', 'https://push.invalid/$2', 'BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM', 'tBHItJI5svbpez7KI4CCXg') returning 1" >/dev/null; }
sub "$TE" te; sub "$HI" hi; sub "$EN" en
```
(Those two long strings are the public example keys of RFC 8291, not secrets.) If the minute has passed, schedule again.

- **5a. Do:** when the job is `done`, read it back as each member (from the server; `-k` because the CA is internal) and play the audio on the laptop:
  ```bash
  S=<session id>; T=$($DC exec -T gateway python -m app.tokens "$TE" </dev/null | tail -1)
  curl -sk https://10.110.11.31:8444/sessions/$S/reminder -H "Authorization: Bearer $T"
  curl -sk -o te.wav https://10.110.11.31:8444/sessions/$S/reminder/audio -H "Authorization: Bearer $T"   # then play te.wav; repeat with $HI, $EN
  ```
- **Expect:** `te`: Telugu text and an `audio_url`; `hi`: Hindi text and audio; `en`: English text and **no audio** (`audio_url` null, the audio route 404).
- **Predicted:** Telugu "30 నిమిషాల్లో ప్రసంగం ప్రారంభమవుతుంది." and Hindi "30 मिनट में एक प्रवचन शुरू होता है।" with audible speech, as the render service produced
  in Phase 2 (the audio then measured RMS 3694 and 5492, silence 0: **a number, not an ear**: a human has never listened). English has no voice in the real render service: **text only**.
  The gateway log will show the pushes **failing** (the endpoints are `push.invalid`): that is intended, and it proves nothing about delivery.
- **Actual (te text / te audio heard? / hi text / hi audio heard? / en):** ____
- **5b. Do (optional, laptop only, not the elder app):** serve `services/gateway/manual_verification/` on the laptop at `http://localhost:8001`
  (a service worker needs a secure context; `localhost` is one), point it at the gateway through an SSH tunnel, paste `VAPID_PUBLIC_KEY` and a user id, subscribe,
  close every tab, then trigger the reminder for that user. The gateway's `/push` is not routed by Caddy, so the tunnel must reach the gateway's own port
  (`docker-compose.yml` does not publish it: add `ports: ["127.0.0.1:18471:8000"]` for the gateway in a throwaway override), and `CORS_ORIGINS` must allow `http://localhost:8001`.
- **Expect:** an OS notification on the laptop with the reminder text.
- **Predicted:** it works on the laptop if the tunnel, CORS and VAPID are right (the Week 3 harness did this once); **on the two phones nothing arrives**, because they never subscribed (finding 1).
- **Actual:** ____

### 6. Go live; confirm the row flips

- **Do:** at the scheduled time (the publish hook accepts any `scheduled` satsang and does not check the clock, per the docstring in `app/satsang.py`; not tried 30 minutes early), start publishing from Larix. Then:
  ```bash
  psqlq "select status, to_char(started_at at time zone 'utc','HH24:MI:SS') from sessions where id='<session id>'"
  ```
  Optional, before touching a phone, **the same player in headless Chrome on the laptop** (needs Node 22+):
  ```bash
  python infra/satsang/rehearsal/listen_check_server.py https://10.110.11.31:8444 8200 &
  node infra/satsang/rehearsal/listen_check_cdp.js "http://127.0.0.1:8200/listen_check.html?sid=<session id>" 45
  ```
- **Expect:** status `live` within a second or two of Larix connecting; the headless check prints `playing` and an RMS near what the speaker's level gives (not 0).
- **Predicted:** `live` at once (the hook is synchronous; Week 9 saw it with ffmpeg, **not timed**). The headless check passes if step 2 is audio only.
- **Actual:** ____

### 7. The elder join screen on a REAL phone

- **Do:** on each phone open `https://10.110.11.31:8444/` (accept the certificate warning on the phone that lacks the CA), finish onboarding, tap the Satsang card, then the live satsang, then the big **Listen** button. Write down the time of the tap and of the first sound: ____ / ____
- **Expect:** "Live now", the big button, then sound, then "You are listening".
- **Predicted:** **Android Chrome:** sound within about 1 to 3 s of the tap (the laptop figure was 0.165 s from `play()` to `playing`, over a tunnel, **not a phone**). Accepting the warning covers the stream because it is the same origin.
  **iPhone Safari:** the first thing to fail. Safari plays HLS natively (or through `ManagedMediaSource` on iOS 17.1 and later: **I do not know which this phone will take**) and does not honour a certificate warning that was clicked through, so I expect "The sound could not be loaded". With the CA trusted I have **no prediction** about the empty H.264 entry in the PMT: Apple's media stack may be stricter than Chrome's, and nothing here tested it.
- **Actual:** ____

### 8. Stop publishing: what the listener sees

- **Do:** with a phone playing, stop the stream in Larix (a clean stop). Note: the time you stopped ____ ; the time the sound ended ____ ; the time the screen changed ____ ; what it said ____ .
  ```bash
  S=<session id>; while true; do printf '%s ' "$(date +%T)"; curl -sk -o /dev/null -w '%{http_code}\n' https://10.110.11.31:8444/hls/$S.m3u8; sleep 5; done   # when does the playlist 404?
  ```
- **Expect:** the row `ended` almost at once; the screen "This satsang has ended".
- **Predicted:** the screen says **ended within 12 s of your stop, and the sound stops with it, 10 s or more before the speaker's last words reach the listener**: the screen stops the player as soon as the list says ended, and the stream is 10 to 15 s behind (finding 3; **a prediction from reading `satsang_listen.py` and from the headless run, not yet seen**). The playlist keeps answering 200 while frozen for about 55 s after the audio would have ended, then 404 (one headless run; #39 recorded about 64 s by another method). If this prediction is right, the closing words of a discourse are lost, and the fix is a separate PR (let the player run until it stalls, then say ended).
- **Actual:** ____

### 9. Glass to glass, measured with a clock

- **Do:** the speaker reads the seconds aloud from the shared clock every 5 s ("twelve forty-one thirty... thirty-five..."). On the listener, write the wall-clock time at which each number is **heard**. Latency = time heard minus time spoken. Do ten readings; write them all.
- **Expect:** about 15 s (the README's figure from the fragment settings: 4 s fragments, a player three behind, plus encode and buffering).
- **Predicted:** **12 to 18 s, centred near 15**. Basis: a Week 9 ffmpeg client measured 15.2 s (one run); hls.js starts three fragments behind the live edge (12 s) plus up to one fragment being cut (0 to 4 s) plus the network. With a long keyframe interval (step 3) more.
- **Actual (ten readings):** ____

### 10. The drop test: a REAL phone losing signal

- **Do:** with the speaker publishing and a listener playing, switch the speaker's phone to **airplane mode** (or walk out of Wi-Fi range). Write the time: ____ . Watch: the admin page's status for the satsang, the listener's screen, and `psqlq "select status, to_char(ended_at at time zone 'utc','HH24:MI:SS') from sessions where id='<id>'"`. After 60 s, restore the connection and try to publish again.
- **Expect:** the media server notices silence and ends the satsang.
- **Predicted:** a radio that goes away does not close the TCP connection, so the server sees silence and `drop_idle_publisher` fires after **30 s**: status `ended` about 30 s after the last data. (Week 9 measured about 30 s with `SIGSTOP`, **which is not a radio drop**; this is the first real one.) The listener hears the sound stop about 10 to 15 s after the drop (the buffer runs out), sees "Waiting for the speaker's voice" 8 s later, and "ended" at the next list refresh after the 30 s mark. After restoring the connection, **publishing is refused** (the satsang has ended and stays ended: OPEN_QUESTIONS #37 and #43). If the phone's OS closes the socket sooner, `ended` comes sooner; write down which it was.
- **Actual:** ____

## After

- [ ] `cd ~/wt-rehearsal && $DC down -v && cd ~ && git -C ~/veerendra worktree remove --force ~/wt-rehearsal && docker volume rm rehearsal_ai_models` (same `DC` and exports as step 1).
- [ ] Send back this file with every **Actual** filled in, the gateway log lines for the satsang (`$DC logs gateway | grep -i satsang`), and photos of the screens in states 7 and 8. **Never paste the admin token, a stream key, or a user id into a chat or an issue.**
