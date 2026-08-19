"""A uvicorn loop factory that yields SelectorEventLoop on Windows.

The real fix for the psycopg/ProactorEventLoop incompatibility -- see
run.py and db.py for the two dead ends tried first (event loop *policy*,
which modern uvicorn ignores entirely).

uvicorn's own uvicorn.loops.asyncio.asyncio_loop_factory hardcodes
ProactorEventLoop on win32 (verified by reading its source: `if
sys.platform == "win32" and not use_subprocess: return
asyncio.ProactorEventLoop`). It's passed straight to `asyncio.run(...,
loop_factory=...)` (Python 3.12+ API), which bypasses
`asyncio.set_event_loop_policy()` altogether -- policy-based overrides
have no effect on it. The only lever that actually works is pointing
uvicorn's `loop=` config at a different factory, which is what this file
provides, referenced from run.py as `loop="loop_factory:factory"`.
"""

import asyncio
import sys


def factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()
