"""Makes pytest add this directory to sys.path, so `from main import app`
works in tests/ regardless of which directory pytest is invoked from.

Also puts backbone/ on the path. In the Docker image, interfaces.py is
copied next to the gateway's own modules (see gateway/Dockerfile), so
`from interfaces import ...` resolves without help; running from the repo
it's still up in backbone/, so this makes host runs match the image. The
real fix is a small shared installable package rather than a path poke --
named as a cost in ADR 0002, not solved here.
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "backbone"))
