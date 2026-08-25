"""Makes pytest add this directory (and backbone/, for `interfaces`) to
sys.path, matching the pattern used by every other service's tests.

Sets env vars BEFORE anything imports app.py, since app.py reads its
Matrix config at module level -- same reasoning as
spike-matrix-a/services/backbone-spike-a/tests/test_bot.py's
os.environ.setdefault() calls before importing app.bot.

Deliberately does NOT override AS_ID, BOT_LOCALPART, AS_TOKEN, HS_TOKEN,
or the bootstrap admin credentials away from app.py's own defaults --
first attempt at this used a separate "_test"-suffixed identity for all
of these, and it broke the moment the tests ran against a Tuwunel
instance where matrix-circle-service's own container had already
bootstrapped: Tuwunel only grants the special "first user -> auto-admin,
auto-joined to the admin room" treatment to the actual first-ever user on
a given homeserver instance, not to "the first time this particular
username was registered". A second, different admin identity registers
successfully as an ordinary user with zero special rooms and can't find
an admin room at all. The tests and the running service registering the
SAME appservice identity is not a compromise -- re-registering an
identical id is Tuwunel's own documented idempotent path (confirmed
manually before relying on it), so this is actually the more honest test:
it exercises the exact identity a real deployment creates, not a parallel
synthetic one. See docs/prompt-journal.md's dated entry.
"""

import os
import sys

os.environ.setdefault("HOMESERVER_URL", "http://localhost:8008")
os.environ.setdefault("SERVER_NAME", "localhost")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKBONE_DIR = os.path.dirname(os.path.dirname(_THIS_DIR))
sys.path.insert(0, _THIS_DIR)
sys.path.insert(0, _BACKBONE_DIR)
