"""Reflex config for the elder client.

PLACEHOLDER NOTICE: this entire clients/elder-app/ tree is a minimal test
client, not Member 1's real UI shell. It exists to prove the
gateway<->client WebSocket wiring works (connect, send, receive,
reconnect-with-backoff) -- Week 4's actual deliverable. Nothing here
should be silently replaced or deleted later; when Member 1's real Reflex
UI shell lands, THIS module's WebSocket connection/reconnect logic
(elder_app/ws_client.py) is the part worth carrying forward into it, not
the bare text-box-and-list UI, which is deliberately not the real design.
See docs/prompt-journal.md's Week 4 entry.
"""

import os

import reflex as rx

# api_url is REFLEX'S OWN internal state-sync backend, unrelated to the
# SatSandesh gateway -- a real bug on the first attempt at this file
# conflated the two, since in the dockerized deployment they happen to
# share one Caddy-fronted origin, which made the mistake easy to miss
# until actually running `reflex run` locally: Reflex's own connection
# broke ("Connection Error" in the browser, and a page-crashing
# `TypeError: Cannot read properties of null (reading 'addEventListener')`
# in the console) because it was pointed at the gateway's address instead
# of its own backend port. See elder_app/elder_app.py for where the
# gateway's own URL is actually used (GATEWAY_PUBLIC_URL, a genuinely
# separate concern).
#
# Left unset here on purpose: Reflex's own default is correct for local
# `reflex run` (its backend on :8000). REFLEX_API_URL overrides it only
# for the dockerized deployment, where it needs to be the origin the
# browser reaches this container's backend through.
config = rx.Config(app_name="elder_app")
if os.environ.get("REFLEX_API_URL"):
    config.api_url = os.environ["REFLEX_API_URL"]
