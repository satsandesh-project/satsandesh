"""Behaviour 4: kill the dispatcher mid-run, restart it, confirm nothing
is lost.

Unlike the other four behaviours, this one genuinely needs a separate OS
process -- "kill it and see what survives" isn't meaningful against an
in-process asyncio task the test itself controls. So this test spawns
`python run.py` (see run.py's docstring for why not `uvicorn` directly)
as a real subprocess, seeds a large batch of messages via confirmed
/send calls *before* the recipient connects (so nothing can be delivered
yet -- see dispatch_once's registry check), then connects the recipient
and hard-kills the process (no graceful shutdown) very shortly after,
deliberately interrupting mid-batch rather than waiting for delivery to
finish. Restarts, reconnects, and confirms everything eventually arrives.

Does not require `docker compose --profile spike up` -- talks to Postgres
on localhost:5432, published by the base (no-profile) `docker compose up`.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import websockets

from db import DATABASE_URL

SPIKE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Large enough, with the dispatcher's 50-row batch size, that draining it
# takes several poll cycles -- a small N tends to finish before we can
# land the kill mid-flight (seen on the first attempt at this test: 15
# messages delivered completely inside an 800ms window, proving nothing
# about interrupting delivery specifically). Logged in the prompt journal.
N_MESSAGES = 300


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(port: int, timeout: float = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _spawn_app(port: int, delivery_delay_ms: int = 0) -> subprocess.Popen:
    env = dict(os.environ)
    env["DATABASE_URL"] = DATABASE_URL
    env["SPIKE_HOST"] = "127.0.0.1"
    env["SPIKE_PORT"] = str(port)
    if delivery_delay_ms:
        env["SPIKE_DELIVERY_DELAY_MS"] = str(delivery_delay_ms)
    proc = subprocess.Popen(
        [sys.executable, "run.py"],
        cwd=SPIKE_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not _wait_for_port(port):
        proc.kill()
        raise RuntimeError("spike app subprocess did not start listening in time")
    return proc


async def _seed_confirmed(port: int, n: int) -> list:
    """Sends n messages via /send, one at a time, waiting for each 200 --
    so every body in the returned list is *confirmed* durably written
    before we do anything else. Recipient isn't connected yet at this
    point, so nothing gets delivered during seeding (see dispatch_once:
    an offline recipient just gets attempts incremented, status stays
    pending)."""
    confirmed = []
    async with httpx.AsyncClient() as client:
        for i in range(n):
            body = f"crash-test-{i}"
            r = await client.post(
                f"http://127.0.0.1:{port}/send",
                json={
                    "conversation_id": "c1",
                    "sender_id": "alice",
                    "recipient_ids": ["dave"],
                    "body": body,
                },
                timeout=5,
            )
            r.raise_for_status()
            confirmed.append(body)
    return confirmed


async def _collect_for(port: int, user_id: str, duration: float) -> list:
    uri = f"ws://127.0.0.1:{port}/ws?user_id={user_id}"
    received = []
    async with websockets.connect(uri) as ws:
        deadline = time.time() + duration
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=min(0.2, remaining))
                received.append(json.loads(msg))
            except asyncio.TimeoutError:
                continue
    return received


def test_crash_mid_dispatch_no_message_lost(spike_clean_db):
    port = _free_port()
    # SPIKE_DELIVERY_DELAY_MS widens the push-then-crash-before-commit
    # window (see dispatcher.py) from microseconds to tens of
    # milliseconds -- without it, a batch of 50 commits fast enough that
    # an external kill lands cleanly on a batch *boundary* every time
    # (confirmed empirically: phase1 always landed at exactly 50/300,
    # zero duplicates -- proving "no loss across a crash" but never
    # actually exercising the in-flight-uncommitted-batch case). Logged
    # in the prompt journal.
    proc = _spawn_app(port, delivery_delay_ms=30)

    try:
        confirmed = asyncio.run(_seed_confirmed(port, N_MESSAGES))
        assert len(confirmed) == N_MESSAGES

        # Recipient connects now -- this is the moment delivery can start.
        # 0.5s lands mid-way through the first batch's ~1.5s (50 rows *
        # 30ms) processing time, guaranteeing the kill hits while that
        # batch's transaction is still open and uncommitted.
        phase1 = asyncio.run(_collect_for(port, "dave", duration=0.5))
    finally:
        proc.kill()  # hard kill -- no graceful shutdown, no clean commit
        proc.wait(timeout=10)

    assert len(phase1) < N_MESSAGES, (
        "everything was delivered before the kill landed -- this run "
        "didn't actually interrupt mid-dispatch, so it isn't proving "
        "what it's supposed to. Re-run, or raise N_MESSAGES / lower the "
        "phase1 collection window."
    )

    port2 = _free_port()
    proc2 = _spawn_app(port2)
    try:
        phase2 = asyncio.run(_collect_for(port2, "dave", duration=15))
    finally:
        proc2.kill()
        proc2.wait(timeout=10)

    all_received = phase1 + phase2
    bodies = [m["body"] for m in all_received]
    expected = set(confirmed)
    got = set(bodies)

    lost = expected - got
    duplicates = len(bodies) - len(set(bodies))

    print(
        f"\n[crash-safety] confirmed_sent={len(confirmed)} "
        f"phase1_received={len(phase1)} phase2_received={len(phase2)} "
        f"lost={len(lost)} duplicates={duplicates}"
    )

    assert not lost, f"messages lost across the crash: {lost}"
    # Duplicates are EXPECTED and OK -- see dispatcher.py's docstring and
    # the ADR. This assertion exists so the number gets printed and
    # reasoned about, not to require zero.
