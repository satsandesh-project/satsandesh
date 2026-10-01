# clients/admin-console/

**Owner:** M4 Sainathan (Stewardship, quality & pilot)
**Status:** Week-7 scaffold. Queue, side-by-side review, release/block and
the audit trail all work — **against committed fixtures, not the gateway.**

Reflex-based moderator console: review queue, original/translation side by
side, one-tap release/block, audit trail. Appeals are Week 9.

## Run

```bash
cd clients/admin-console && python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

```bash
PYTHONPATH=../.. ./.venv/Scripts/python.exe -m reflex run
```

Port **3001** — the elder app owns 3000.

## Why it runs on fixtures

The routes this console needs — `GET /moderation/queue`,
`POST /moderation/messages/{id}/release|block`,
`GET /moderation/messages/{id}/events` — **do not exist yet**. They are M2's
side of [issue #65](https://github.com/satsandesh-project/satsandesh/issues/65),
with the wire shape proposed in PR #81 (`contracts/chat/moderation.py`).

`admin_console/source.py` has both backends behind one protocol:

| | |
|---|---|
| `FixtureQueueSource` | Default. Reads `admin_console/fixtures/queue.json`, mutates in memory, nothing persists across a restart. |
| `GatewayQueueSource` | The real one, written against the proposed contract. Swapping it in is a config change, not a rewrite. Not covered by tests — there is nothing to test it against yet, and mocking the endpoint whose shape is still under review would test this file's guess rather than the gateway. |

Both validate every payload against `contracts/chat/moderation.py`, so a
drifted fixture fails loudly on load instead of rendering a broken row.

## Three things that are requirements, not styling

Any redesign has to keep these. They come from the proposal (§7.3, §15),
not from taste.

1. **Original and English pivot are always shown together.** A moderator
   reading only the pivot is reviewing a *translation* of what the sender
   wrote, and devotional idiom is exactly where that breaks — the
   "I want to kill my ego" fixture is there to make the point. When the
   pipeline failed before producing a pivot, the console says so rather
   than showing an empty panel.
2. **A fail-closed hold looks different from a judgement.** When the
   classifier timed out or returned nonsense there is no model opinion to
   weigh; showing `confidence 0.00` as if it were a verdict would mislead
   the moderator into deferring to a machine that never answered.
3. **The notice the sender received is shown.** "Never silently"
   (proposal §15) is a promise about the sender's experience. A moderator
   should be able to see what the sender was actually told — and
   `ModerationReviewOut.notice_sent` surfaces the case where an action
   stands but the notice did not go out.

## Concurrency

Two moderators can have the same item open. Every review sends
`expected_event_id`; a stale one is refused with `ReviewConflict` and the
console shows a banner and refreshes, rather than silently overwriting a
decision this moderator never saw. The fixture backend raises the same
conflict when the message has already left the queue.

## Tests

```bash
PYTHONPATH=../.. ./.venv/Scripts/python.exe -m pytest tests/ -q
```

12 tests, no network: fixtures validate against the contract; the queue
covers text, a voice note and a degraded hold; a decision appends to the
trail and leaves the queue (append-only — nothing is replaced); a label
correction is recorded and an uncorrected label stands; a stale decision
is refused with the trail untouched; and the state surfaces a conflict
instead of applying it.

## Not done yet

- **Role gating.** Every route here will need `require_role("moderator",
  "admin")` on the gateway side; the console assumes it is reachable only
  by a moderator and does not enforce that itself. Tracked in
  `docs/security-checklist.md` B5.
- **Appeals** (Week 9) — an appeal is more events on the same message plus
  a thread the sender can read.
- **Not in CI.** No client is, including `clients/elder-app/`. Worth one
  job for both rather than one each.
- **Live wiring** — blocked on #65.
