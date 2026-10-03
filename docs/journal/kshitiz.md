# Prompt Journal — Kshitiz (M1, elder client)

Per `docs/CONVENTIONS.md`'s rule ("a prompt journal entry added under
`docs/journal/<your-name>.md` for anything notable") — this file didn't
exist yet; starting it here rather than adding to `docs/prompt-journal.md`,
which is Veerendra's own file (`# Prompt Journal — Student 2 (Platform &
backbone)`), not a shared one.

## Week 5 — accessibility pass, and a real review that caught two real bugs

**What shipped (PR #45):** two Month-1 server-side features that were
never reachable from the client — quiet hours (`GET`/`PATCH
/me/settings`, blocked on Veerendra building that endpoint; the client
side is written against the contract shape and fails soft until it
exists) and tap-and-hold-to-hear audio labels on five buttons (Back,
Send, the Satsang tab, +Add someone, quiet-hours Save), backed by the
existing `GET /audio-labels/{label}` endpoint from Week 4. Undo was
already fully wired — nothing to add there.

**Veerendra reviewed it and requested changes — for real reasons, not
nitpicks.** Two confirmed bugs:

1. **Double-fire.** The original wiring kept each button's existing
   `on_click` alongside new `on_mouse_down`/`on_mouse_up` handlers for the
   hold-to-preview gesture. Didn't think this through carefully enough at
   the time: a mouseup on the same element the mousedown started on
   *always* fires a click in the browser, no matter how long the press
   lasted. So holding "Send" long enough to hear it say "Send" also
   actually sent the message — the exact opposite of what a preview
   affordance is supposed to guarantee. Confirmed the mechanism myself
   before fixing it, not just taken on the review's word.

   Fixed by removing every `on_click` on these five buttons and routing
   the real action through `on_mouse_up` only, gated on a JS check
   (`AUDIO_LABEL_HOLD_RELEASE_JS`) that reports "short" (timer never
   fired — safe to run the real action) or "held" (the preview already
   played — suppress it). Added a real regression test for this
   (`tests/test_state.py`) — 14 tests, all passing, covering both branches
   for all five buttons plus the settings-load parsing.

2. **Telugu silently gets English audio.** The gateway's audio-label
   catalog only ever had `en`/`hi` entries; the client only offers
   `en`/`te`. So a Telugu-preference elder holding a button would always
   get the English clip with no error, no indication. Checked
   `services/gateway/app/audio_labels.py` before agreeing this was
   real: today it's a placeholder sine-tone stub either way (not real
   speech yet, per that file's own Week-4-phase docstring), so it's
   currently inaudible as a difference — but it's a genuine, latent
   correctness gap that would silently break the moment real TTS lands.
   Added `te` entries for all five labels (`services/gateway/`, flagged
   to Veerendra as touching his lane since this repo's ownership of that
   path is still the CODEOWNERS `*` fallback, not solely mine).

Four smaller things the review also caught, all fixed: no tests existed
for the new feature (added, see above); the same three-line
`on_mouse_down`/`up`/`leave` block was hand-copied across all five
buttons (collapsed into one `hold_to_hear()` helper); the audio blob's
object URL was never released (`URL.revokeObjectURL` now runs on
playback end or failure); and this journal entry itself, which the
team's own convention already asked for and I hadn't done yet.

**A CI gap noticed along the way, not fixed here.** Running the new
`tests/test_state.py` showed root `pyproject.toml`'s `testpaths =
["tests"]` means plain `pytest` from the repo root won't discover
`clients/elder-app/tests/` at all — same class of blind spot Veerendra's
PR #51 just found and fixed for `services/gateway/`'s 206 tests. Left
this one for a follow-up rather than expanding this PR's scope further;
noting it here so it isn't lost.

**Process note, since it's directly relevant to this same PR:** the
Reflex client-side tooling on my own Windows machine hits a genuine
upstream bug in `@react-router/dev` (`restartWithMergedOptions` crash,
reproduced on a clean venv, persists after upgrading reflex) that stops
`reflex run` from ever serving locally here. Verified this PR's changes
by instantiating every touched component directly in Python instead
(catches render/prop errors, not visual/interaction bugs) — which is
exactly why a real human review caught what that verification couldn't:
the double-fire bug is an interaction bug, invisible to a component
render check. Worth remembering next time I'm tempted to treat "it
compiles" as "it works."

## Week 6 — voice capture: record, upload, send

**What shipped:** the mic button already recorded and previewed locally
since Month 1 (per this file's own module docstring: "It does not send
anywhere: there is no backend endpoint for voice notes yet"). Week 6
closes that gap for real — upload to the actual `POST /media`
(`services/gateway/app/media.py`, merged this week via #61/#63, not a
mock) and send as a genuine `kind: "voice"` message over the same WS
path text messages already use.

**Design choices, and why:**

- **Moved the mic button and recording banner from the Home screen into
  the chat screen.** They floated on Home with no send target before —
  which is exactly why sending was never wired up; there was nothing to
  send *to*. Now they live where `current_contact_id`/`current_circle_id`
  actually gives a real destination.
- **Reused `sendMessageFrame` rather than building a parallel voice-send
  path.** Its signature changed from a bare `text` argument to a
  `payload` object (`{kind, text}` for text, `{kind, media_ref}` for
  voice) — one function, one pending/ack/status/resend machinery for
  both kinds, instead of two codepaths that could drift.
- **"Upload with progress" is stage progress (recording → uploading →
  sent/failed), not a byte-percentage bar.** Checked PR #63's own
  numbers first: ~100KB for a 30-second note. A progress bar for a
  sub-second upload wouldn't show anything meaningful; clear stage
  feedback is what actually matters at this size. Documented as a
  deliberate scope call, not a shortcut I'm hiding.
- **Retry: one silent retry, then a manual "tap to retry" that keeps the
  recording.** Mirrors `app/auth.py`'s own retry-once-then-surface
  pattern server-side. The recording survives a failed upload — losing
  the whole note to one dropped request would be a real elder-hostile
  failure mode on the networks this app targets.
- **Voice playback in the thread is a plain `<audio>` element, nothing
  more.** Speed control, autoplay, "original always one tap away" are
  explicitly Week 7's job ("Receiver experience"), not this week's
  ("Voice capture" — sending, not receiving nicely).

**Verification:** every touched component instantiated directly in a
throwaway venv (same limitation as Week 5 — `reflex run` still doesn't
serve locally here), plus 9 tests in `tests/test_state.py` (4 carried
over from Week 5, 5 new): the upload/send state transitions
(uploading → ok clears the recording; uploading → failure keeps it for
retry; discard resets both fields). The upload and retry logic itself
lives in JS (`UPLOAD_AND_SEND_VOICE_JS_TEMPLATE`) and isn't practical to
unit test headless, same reasoning as Week 5's click-guard — this is the
Python-side coverage that's actually possible without a browser.

**Caught my own mistake before it shipped:** first draft of the
structural on_click regression test (from Week 5) started failing after
this change — not because of a real regression, but because it checked
for *any* `on_mouse_down`, which now also matched the mic button's own
`on_mouse_down=start_recording` (correctly has no `on_click` — it's a
press-and-hold record control, not a tap action). Fixed by checking for
the specific `start_audio_label_hold` handler instead of any
`on_mouse_down` at all, so the test means what it says again.

## Week 7 — language and TTS preferences; a real gap flagged before coding

**Stopped before building the whole task.** Week 7's "text and audio
together" needs translated renderings — a message shown in the
receiver's own language, with audio. Checked first: `contracts/chat/`
has no shape for this at all, and the client never talks to
`services/ai/` directly, only the gateway — so there was nothing
concrete, real or mock, to build that part against. M2's own Week 7 task
is exactly what would define how renderings reach the client, and it
doesn't exist yet either. Asked rather than either inventing a
contract shape solo or building UI with no data behind it — user chose:
build the genuinely independent parts now, leave the rendering-dependent
part as a tracked gap rather than guessing at someone else's contract.

**What shipped instead:** the two parts of Week 7 that never needed the
renderings gap at all — a content-language picker (en/hi/te, the
language incoming voice notes should be translated into) at onboarding
and in settings, and a TTS on/off toggle. Both fold into the same
settings card and `/me/settings` PATCH Week 5 already built (still
pending M2's real endpoint — same "fails soft" note as before, one more
field on an already-waiting request).

**A naming decision worth recording:** `preferred_language_input` is
deliberately a *different* field from `State.language` (this app's own
UI chrome toggle, en/te only, unchanged since Month 1). Conflating them
would mean a Telugu-reading elder couldn't ask for English audio, or
vice versa — the two are genuinely different questions ("what language
do you read this app in" vs "what language do you want incoming voice
notes translated into"), and LanguageCode's three values (en/hi/te)
don't even match the UI chrome's two. Defaults `preferred_language_input`
to whatever UI language is active at join time, as a sensible starting
point, without ever forcing the two to stay in sync afterward.

**Renamed `quiet_hours_card` → `settings_card`, `save_quiet_hours` →
`save_settings`.** Week 7 extends the same card and the same PATCH
call with two more fields — a card called "quiet hours" holding a
language picker would be actively misleading, more so than the small
churn of renaming its handful of call sites (fixed in the same commit,
including a stale `she` pronoun for Veerendra a Week-5-era comment had
never caught).

**Verification:** same approach as Weeks 5–6 — every touched component
instantiated in a throwaway venv (`reflex run` still doesn't serve
locally here), plus 6 new tests in `tests/test_state.py` (15 total):
parsing the two new fields from a settings response, leaving them alone
when the server hasn't sent them yet (must not silently reset a
join-time choice), the two setters clearing the saved flag, and
`join_circle` actually including the settings-save call so the picked
language persists past the onboarding screen.

## 2026-10-03 — Weeks 5/6 audit against the dependency map: touch screens and upload progress

Re-read M1's Week 5 and Week 6 deliverables in the Month 2-3 dependency map
against the code, rather than against the PRs that built them. Two real gaps,
both invisible on a laptop:

**Hold-to-record and hold-to-hear were mouse-only.** The mic button and the five
buttons that read their label aloud used `on_mouse_down`/`up`/`leave`. A phone or
tablet fires `mousedown` only after a tap is released, so on a touch screen the mic
recorded for zero seconds and no hold was ever seen -- on exactly the devices the
elders use. Reflex 0.9.9 has no touch or pointer triggers (checked the installed
package), so `TOUCH_HOLD_SHIM_JS` translates touchstart/touchend into the mouse
events those buttons already handle, keyed on a `data-sat-hold` attribute
("press" for the mic, which owns the whole gesture; "label" for the others, which
keep the browser's normal tap/click). CSS stops text selection and the long-press
menu from cancelling the hold.

**Voice upload had no progress display** (the Week 6 line says "upload with
progress and retry"). It now uses `XMLHttpRequest` (fetch cannot report upload
progress) and a progress bar written straight to the DOM, and makes three
attempts (1s, 2s pauses) instead of two.

**Verification:** on my own demo stack (port 18300, not the shared 8095) with the
browser pane at 375x812, synthetic touch events: a 100 ms tap on a label button
fetched nothing, a 700 ms hold fetched `/audio-labels/circle`, and the guard flag
and timer were clear afterwards; a 1.6 s touch hold on the mic recorded a 21 KB
clip; the upload bar went 0% -> 100% with `role=progressbar`; with the first two
upload attempts forced to fail, the third succeeded and the message sent. 3 new
tests in `tests/test_state.py` (18 total) pass in the app's own container.

**Not verified:** a real finger on a real phone, and the real microphone -- the
demo is plain HTTP, where `getUserMedia` is blocked, so recording was tested with
a stubbed audio stream. Synthetic touch events are not the same as a physical
touch; the first real-device check is still owed.

## 2026-10-03 — Week 7 part 2: the receiver experience

The dependency map's Week 7 M1 line: text and audio together, original always one
tap away, 0.8-1.2x speed control, opt-in autoplay, language pick that holds, per-user
TTS on/off. Part 1 (#68) did the pickers; this is the screen itself. It builds
against `MessageOut.renderings` / `transcript` (#83, #86), which the gateway will
fill from #87/#88.

**What a message shows.** If it carries a rendering in the receiver's content
language: that text, plus its audio when speech is on, plus a button to flip to the
original (the voice note itself and its transcript). If not (same language, or none
made): the original, as before. A degraded rendering says so ("No audio for this
message", "Approximate translation") instead of looking broken. With speech off the
audio and its note disappear and the text stays -- the useful mode for an elder who
finds audio hard to follow.

**Playback was broken against the real gateway, independent of any of this.** The
old `<audio src=GATEWAY/media/id>` cannot send the `Authorization` header that `GET
/media/{id}` has always required, so every received voice note would have been
refused. Audio is now fetched with the token and played from a blob URL (cached per
media id; a failed load shows a tap-to-retry line).

**Speed and autoplay.** 0.8x / 1x / 1.2x buttons (44 px targets), remembered on the
device. Autoplay is opt-in (off by default) from the settings card, and starts only
the newest voice message from someone else. `renderCurrentThread` rebuilds the whole
thread on every update, which used to cut off a playing note; the playing note now
resumes where it was.

**"Holds".** The gateway's `/me/settings` still doesn't exist, so the preferences
(content language, speech on/off, autoplay) are also kept in localStorage; they
survive a reload on this device. Server values still win once the endpoint exists.

**Verification:** on my demo stack (port 18300) at 375x812 against the real gateway:
my own two voice notes played from blob URLs with real durations; speed 1.2x applied
to every player and was saved; injected incoming messages (the gateway doesn't emit
renderings yet) showed the translated view, the original + transcript view, the
no-rendering fallback, the "approximate" and "no audio" notes, and speech-off; autoplay
started the marked message at 1.2x and kept playing across a re-render (0.44 s -> 0.87
s on a new element); a bogus media id showed the retry line; language + autoplay held
across a reload. 5 new tests in `tests/test_state.py` (20 total) pass in the app's
container.

**Not verified:** a real translated message end to end (needs #87/#88 deployed; I
used injected messages), a real phone, and the Telugu strings -- I wrote them and they
need a native reader's eye before the pilot. Blob URLs live for the page's lifetime;
fine for a session of voice notes, worth a revoke-on-leave later.
