"""Entrypoint for running the spike as `python run.py`, instead of
`python -m uvicorn app:app`.

Why not the plain CLI: psycopg's async mode refuses Windows' default
ProactorEventLoop. The obvious fix -- set a custom asyncio event loop
*policy* before uvicorn starts -- does NOT work with this uvicorn version
(0.52.1): uvicorn resolves its own loop via `get_loop_factory()` and
passes it straight to `asyncio.run(..., loop_factory=...)`, which
bypasses the global policy entirely. See loop_factory.py for the actual
fix and the full story -- two dead ends before landing on this one,
logged in the prompt journal.
"""

import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("SPIKE_HOST", "0.0.0.0")
    port = int(os.environ.get("SPIKE_PORT", "8000"))
    uvicorn.run("app:app", host=host, port=port, loop="loop_factory:factory")
