"""Reflex config for the elder client.

Merged from two independent branches of Week 4 work: Member 1's real UI
shell (plugins below) and Member 2's WebSocket wiring / deployment fixes
(REFLEX_API_URL handling below) -- see docs/prompt-journal.md's Week 4
entries on each side for the full history.
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
# of its own backend port. See elder_app/gateway_ws_proof.py for where
# the gateway's own URL is actually used (GATEWAY_PUBLIC_URL, a genuinely
# separate concern) -- not elder_app.py, which isn't wired to the gateway
# yet (see that module's own docstring for why).
#
# Left unset here on purpose: Reflex's own default is correct for local
# `reflex run` (its backend on :8000). REFLEX_API_URL overrides it only
# for the dockerized deployment, where it needs to be the origin the
# browser reaches this container's backend through.
config = rx.Config(
    app_name="elder_app",
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ],
)
if os.environ.get("REFLEX_API_URL"):
    config.api_url = os.environ["REFLEX_API_URL"]
