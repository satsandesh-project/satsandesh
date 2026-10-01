"""Reflex config for the moderator console.

Same shape as clients/elder-app/rxconfig.py -- `api_url` is Reflex's own
state-sync backend, not the SatSandesh gateway. Conflating the two broke
the elder app once (see that file's comment); the gateway's URL belongs in
admin_console/source.py's GatewayQueueSource, which is a separate concern.

Port 3001: the elder app already owns 3000.
"""

import os

import reflex as rx

config = rx.Config(
    app_name="admin_console",
    frontend_port=3001,
    plugins=[rx.plugins.TailwindV4Plugin()],
)
if os.environ.get("REFLEX_API_URL"):
    config.api_url = os.environ["REFLEX_API_URL"]
