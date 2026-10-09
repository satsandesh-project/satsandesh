# clients/admin-console/

**Owner:** M4 Sainathan (Stewardship, quality & pilot)
**Status:** Week-7 console. Queue, side-by-side review, release/block and
the audit trail all work, against **fixtures by default** and against the
**chat mock or the real gateway** by configuration.

Reflex-based moderator console: review queue, original/translation side by
side, one-tap release/block, audit trail. Appeals are Week 9.

## Run

```bash
cd clients/admin-console && python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

```bash
PYTHONPATH=../.. ./.venv/Scripts/python.exe -m reflex run
```

Port **3001** — the elder app owns 3000. With nothing configured it runs on
the committed fixtures and says so in the header.

## Choosing where the queue comes from

`admin_console/source.py` holds two backends behind one protocol, picked by
the environment, not by editing code:

| Variable | |
|---|---|
| `CONSOLE_SOURCE` | `fixture` (default) or `gateway` |
| `CONSOLE_GATEWAY_URL` | e.g. `http://localhost:8002` — gateway only |
| `CONSOLE_GATEWAY_TOKEN` | the Bearer token — gateway only; never shown in the UI or in an error |
| `CONSOLE_GATEWAY_HEADERS` | optional JSON object of extra headers |

A bad value fails at startup naming the variable, not on the first click.
(The previous version of this README said swapping to the gateway was "a
config change". It was not: the source was built at import time and nothing
read a setting.)

**The fixtures** (`admin_console/fixtures/queue.json`, generated *from*
`contracts/chat/moderation.py`) are kept for what the mock cannot cover: a
**voice note**, and a **fail-closed hold with no pivot** — the chat mock never
queues a voice note. They mutate in memory and nothing persists across a
restart. They default to what the real gateway does today — **no sender
notice is delivered** — so the console shows the "sender has NOT been
notified" warning on every action, because that is currently true.
`FixtureQueueSource(notices_delivered=True)` shows the happy path.

**Against the chat mock** (`contracts/chat/mock/`, PR #105/#106), which
serves the same four routes with the same rules:

```bash
./.venv/Scripts/python.exe -m uvicorn contracts.chat.mock.app:app --port 8002
```

```bash
CONSOLE_SOURCE=gateway CONSOLE_GATEWAY_URL=http://localhost:8002 CONSOLE_GATEWAY_TOKEN=mock CONSOLE_GATEWAY_HEADERS='{"X-Mock-Role": "moderator"}' PYTHONPATH=../.. ./.venv/Scripts/python.exe -m reflex run
```

Send a text message containing `hold` to the mock to put something in the
queue. Its limits are its own README's: a moderator is whoever sends the
header, the pivot is a fake `[en] <text>`, there is no WebSocket push.

**Against the real gateway.** Two things are easy to get wrong:

- **`/moderation*` is routed through Caddy, and the gateway accepts only a
  signed token there**, in every `AUTH_MODE` (PR #125): a bare user id, which
  the legacy auth treats as that user, gets 401 "Moderation requires a signed
  token" before anything else runs. So `CONSOLE_GATEWAY_TOKEN` must be a signed
  one. The console itself is still not served by the compose stack.
- **The token must belong to a user whose `users.role` is `moderator` or
  `admin`**; the gateway checks the *database* role on every request. A signed
  token comes from an operator: `python -m app.tokens <user-uuid>`. The same
  token is what the console needs to play a held voice note
  (`GET /media/{id}`), which also requires a signed token since #126. What a
  token still does not do — no revocation, 30-day lifetime — is
  `services/gateway/OPEN_QUESTIONS.md` #33. Treat a moderator token as the most
  valuable credential in the system.

## Failures a moderator will actually meet

Every failure that is not a review conflict becomes a `SourceError` with a
message written to be shown as-is, and a red banner. Without it each is an
unhandled exception inside a Reflex handler: the moderator clicks Release
and nothing happens, with no word about why.

| What happened | What the moderator sees |
|---|---|
| 401 | the token was not accepted — wrong, expired, or not a signed token |
| 403 | this account is not a moderator on that gateway |
| 404 | that message does not exist (any more) |
| 5xx / other 4xx | the gateway's status and its own `detail` |
| unreachable | could not reach the gateway; check the address and the network |
| timeout | did not answer in time; **nothing was changed**, try again |
| wrong shape | the gateway may be on a different contract version |
| 409 | a **review conflict**, below — not an error |

A failed *refresh* keeps the last queue on screen with a warning rather than
blanking it: an empty list would read as "nothing is waiting", the one wrong
thing to imply when the truth is "I could not ask". A failed refresh *after*
a successful decision shows both messages; letting the second overwrite the
first would make a release that worked look like it had not.

## The queue is paged

The real `GET /moderation/queue` returns 25 items by default (100 at most)
and a `next_cursor`. `GatewayQueueSource.fetch_queue` follows the cursor to
the end. The first version made one request and dropped the cursor, so with
more than 25 held messages a moderator would clear the first page and see an
empty queue while more were still waiting — silent data loss in the one place
the product promises a human sees every hold. A server that keeps returning
the same cursor is an error, not an infinite loop.

## Three things that are requirements, not styling

Any redesign has to keep these. They come from the proposal (§7.3, §15), not
from taste.

1. **Original and English pivot are always shown together.** A moderator
   reading only the pivot is reviewing a *translation* of what the sender
   wrote, and devotional idiom is exactly where that breaks — the "I want to
   kill my ego" fixture is there to make the point. When the pipeline failed
   before producing a pivot, the console says so rather than showing an empty
   panel.
2. **A fail-closed hold looks different from a judgement.** When the
   classifier timed out or returned nonsense there is no model opinion to
   weigh; showing `confidence 0.00` as if it were a verdict would mislead the
   moderator into deferring to a machine that never answered.
3. **The notice the sender received is shown.** "Never silently" (proposal
   §15) is a promise about the sender's experience. A moderator should see
   what the sender was actually told — and `ModerationReviewOut.notice_sent`
   surfaces the case where an action stands but the notice did not go out.

## Concurrency

Two moderators can have the same item open. Every review sends
`expected_event_id`; a stale one is refused with `ReviewConflict` and the
console shows a banner and refreshes, rather than silently overwriting a
decision this moderator never saw. The real gateway answers 409 both for a
stale id and for "the message is not in a state that action accepts", so one
banner covers both.

## Tests

```bash
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

```bash
PYTHONPATH=../.. ./.venv/Scripts/python.exe -m pytest tests/ -q
```

36 tests, no network and no database. `requirements-dev.txt` adds `pytest`
and `fastapi`: the tests drive `GatewayQueueSource` against the chat mock
in-process, which is a FastAPI app, and Reflex does not bring it.

Coverage: fixtures validate against the contract; a decision appends to the
trail and leaves the queue (append-only); a stale decision is refused with the
trail untouched; the **paging loop** (three pages, no overlap or duplicates,
and a cursor-repeating server stops); every failure above reads as a
person-readable message; a failed refresh keeps the queue; and the backend is
chosen and validated from the environment, with the token kept out of the
label and out of errors.

The paging test was checked by mutation — the original bug reintroduced and
the test confirmed to fail. That check caught one test that *claimed* to cover
it but passed with the bug present; it was renamed to say what it actually
checks.

## Not done yet

- **The console has no login of its own.** It acts with the one token in
  `CONSOLE_GATEWAY_TOKEN`. The gateway enforces the rest (a signed token and a
  moderator or admin `users.role`, so a wrong token gets 401 or 403), but
  anyone who can open the console in a browser can use that token's powers:
  keep it on the network, not public. `docs/security-checklist.md` B5 tracks it.
- **A voice note's transcript is not displayed yet.** `ModerationQueueItem`
  carries `transcript` / `transcript_language` (contracts/chat 0.6.0), kept
  apart from `original_text` on purpose: one is what the sender typed, the
  other is what the machine heard, and that is the thing that can be wrong.
  The console should show the audio, the transcript and the pivot as three
  separate things.
- **Not in CI**, and neither is `clients/elder-app/`. One job covering both is
  worth more than one each. This is how a missing `pydantic` in
  `requirements.txt` went unnoticed until a reviewer installed it clean.
- **Appeals** (Week 9) — more events on the same message plus a thread the
  sender can read.
