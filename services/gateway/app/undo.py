"""Week 4 Phase 8: in-memory registry of pending fan-out tasks backing the
30-second undo window. A message is created `pending` and its real delivery
(app/messages.py's `fan_out_message`) is deferred here by `delay` seconds;
`cancel_fan_out` lets `DELETE /messages/{id}` pull it back before it runs.

In-memory, single-process only, keyed by message_id — a production
deployment serving more than one gateway process would need a distributed
task queue (e.g. Celery/Redis) instead, since a task scheduled on process A
is invisible to an undo request that lands on process B. Fine for this
phase: nothing here assumes more than one process yet, same standing
limitation app/ws.py's ConnectionManager already documents for itself.
"""

import asyncio
from asyncio import sleep as asyncio_sleep
from collections.abc import Coroutine
from typing import Any

_pending: dict[str, asyncio.Task] = {}


def schedule_fan_out(
    message_id: str, delay: float, fan_out_coro: Coroutine[Any, Any, None]
) -> None:
    """Schedule `fan_out_coro` to run after `delay` seconds. Idempotent: if
    a task is already registered for `message_id` (a dedup retry of the
    same send), the new coroutine is discarded — closed explicitly so it
    doesn't raise a "coroutine was never awaited" warning — rather than
    scheduling a second delivery for the same message."""
    if message_id in _pending:
        fan_out_coro.close()
        return
    _pending[message_id] = asyncio.create_task(_run_after(message_id, delay, fan_out_coro))


def cancel_fan_out(message_id: str) -> bool:
    """Cancel the pending task for `message_id`, if any. Returns True if a
    task was found and cancelled, False if none was pending — already
    fanned out, already cancelled, or never scheduled. Safe to call in any
    of those cases."""
    task = _pending.pop(message_id, None)
    if task is None:
        return False
    task.cancel()
    return True


async def _run_after(message_id: str, delay: float, coro: Coroutine[Any, Any, None]) -> None:
    try:
        try:
            await asyncio_sleep(delay)
        except asyncio.CancelledError:
            # cancel_fan_out cancelled us during the sleep, before `coro`
            # (fan_out_message) ever started running — it was constructed
            # by the caller but never awaited or closed anywhere else, so
            # without this it leaks and Python flags it (usually much
            # later, at interpreter/loop shutdown) as "coroutine was never
            # awaited". Close it here instead, then let the cancellation
            # keep propagating normally.
            coro.close()
            raise
        await coro
    finally:
        # Covers the task completing on its own (the common case) — a
        # cancellation instead goes through cancel_fan_out's own pop, so
        # this is a no-op there.
        _pending.pop(message_id, None)
