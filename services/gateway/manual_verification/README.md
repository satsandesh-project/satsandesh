# Manual verification harness — Week 3 Phase 7 (Web Push)

**This is throwaway scaffolding, not a product surface.** It exists purely
to let a human confirm, with a real browser, that `POST /push/subscribe`
plus `app/push.py::send_push` produce an actual OS-level notification —
something the automated suite (`tests/test_push.py`,
`tests/test_push_routes.py`, `tests/test_ws_delivery.py`'s push-trigger
tests) proves with a mocked `pywebpush.webpush`, deliberately, since a real
push round-trip needs a real browser and a real push service, neither of
which belong in CI. See `PROMPT_JOURNAL.md`'s Week 3 Phase 7 entries for
the full reasoning.

It is never imported by `app/`, never wired into `app/main.py`, and is
excluded from the installable package by `pyproject.toml`'s
`[tool.setuptools.packages.find]` (`include = ["app*"]`) the same way
`tools/` already is — nothing here ships.

## Files

- `index.html` — subscribes the browser to push via the Push API, using a
  VAPID public key and bearer token you paste in (never hardcoded, so
  nothing environment-specific lives in a committed file), then POSTs the
  resulting subscription to the real gateway's `/push/subscribe`.
- `sw.js` — the service worker that actually receives the `push` event
  and calls `showNotification` — this is what makes the OS-level
  notification appear, from a tab that may not even be open.

## How to use this

See `PROMPT_JOURNAL.md`'s Week 3 Phase 7 "manual verification" entry, or
ask for the step-by-step again — the short version: serve this directory
with a plain static server on its own port (a service worker needs to be
served over http(s), not opened as a `file://` URL), point `index.html` at
your running gateway, paste in `VAPID_PUBLIC_KEY` from `.env` and a real
user's UUID as the token, subscribe, close every tab, then trigger a
message to that user from a second identity.
