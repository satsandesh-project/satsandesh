"""
Where the console gets its queue from.

Two backends behind one protocol:

  FixtureQueueSource  -- reads committed JSON fixtures, mutates in memory.
                         The default, and what the Week-7 scaffold runs on,
                         because the gateway routes this console needs
                         (GET /moderation/queue, POST .../release|block,
                         GET .../events) do not exist yet -- that is M2's
                         side of issue #65, proposed as contracts/chat/
                         moderation.py in PR #81.

  GatewayQueueSource  -- the real one. Written against the proposed
                         contract so that swapping it in is a config
                         change, not a rewrite. Not exercised by the test
                         suite: there is nothing to exercise it against
                         yet, and a test that mocks the very endpoint
                         whose shape is still under review would be
                         testing this file's guess, not the gateway.

Every payload crossing this boundary is validated against
contracts/chat/moderation.py in both backends. A fixture that drifts from
the contract fails loudly here rather than rendering a broken row.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx

from contracts.chat.moderation import (
    ModerationAction,
    ModerationActorKind,
    ModerationEvent,
    ModerationEventsOut,
    ModerationQueueItem,
    ModerationQueueOut,
    ModerationReviewIn,
    ModerationReviewOut,
)

FIXTURES = Path(__file__).parent / "fixtures"


class ReviewConflict(RuntimeError):
    """Someone else decided this message first.

    Raised when `expected_event_id` no longer matches the message's latest
    event. The console must show this rather than retrying: two moderators
    had the same item open, and the second one's click would otherwise
    silently overwrite a decision they never saw.
    """


class QueueSource(Protocol):
    def fetch_queue(self) -> ModerationQueueOut: ...

    def fetch_events(self, message_id: uuid.UUID) -> ModerationEventsOut: ...

    def review(
        self, message_id: uuid.UUID, *, release: bool, body: ModerationReviewIn
    ) -> ModerationReviewOut: ...


class FixtureQueueSource:
    """Committed fixtures, mutated in memory. Nothing persists across a
    restart -- deliberately: this is a scaffold for building the UI
    against, not a stand-in datastore."""

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self._dir = fixtures_dir or FIXTURES
        raw = json.loads((self._dir / "queue.json").read_text(encoding="utf-8"))
        # Validated against the contract on load: a drifted fixture is a
        # loud failure here, not a broken row in the UI.
        self._queue = ModerationQueueOut.model_validate(raw)
        self._items: dict[uuid.UUID, ModerationQueueItem] = {
            item.message_id: item for item in self._queue.items
        }
        self._events: dict[uuid.UUID, list[ModerationEvent]] = {
            item.message_id: [item.latest_event] for item in self._queue.items
        }
        trail_path = self._dir / "events.json"
        if trail_path.exists():
            for message_id, events in json.loads(trail_path.read_text(encoding="utf-8")).items():
                key = uuid.UUID(message_id)
                if key in self._events:
                    self._events[key] = [ModerationEvent.model_validate(e) for e in events]

    def fetch_queue(self) -> ModerationQueueOut:
        return ModerationQueueOut(items=list(self._items.values()))

    def fetch_events(self, message_id: uuid.UUID) -> ModerationEventsOut:
        return ModerationEventsOut(
            message_id=message_id,
            events=sorted(self._events.get(message_id, []), key=lambda e: e.created_at),
        )

    def review(
        self, message_id: uuid.UUID, *, release: bool, body: ModerationReviewIn
    ) -> ModerationReviewOut:
        item = self._items.get(message_id)
        if item is None:
            # Not in the queue. Two very different reasons, and the console
            # must be able to tell them apart: either someone else already
            # decided it (a conflict -- refresh and move on), or this id
            # was never real (a bug). The real gateway sees the same split,
            # because a decided message still exists there with a changed
            # status; only this in-memory queue drops the row.
            if message_id in self._events:
                raise ReviewConflict(
                    f"message {message_id} was already decided by someone else "
                    "and has left the queue"
                )
            raise KeyError(f"no queued message {message_id}")

        latest = self._events[message_id][-1]
        if body.expected_event_id is not None and body.expected_event_id != latest.id:
            raise ReviewConflict(
                f"message {message_id} was decided by someone else "
                f"(expected event {body.expected_event_id}, latest is {latest.id})"
            )

        action = ModerationAction.ALLOW if release else ModerationAction.BLOCK
        event = ModerationEvent(
            id=uuid.uuid4(),
            message_id=message_id,
            actor_kind=ModerationActorKind.MODERATOR,
            actor_id=_DEMO_MODERATOR_ID,
            # A moderator's correction wins; otherwise the classifier's
            # label stands and only the action changes.
            label=body.label or latest.label,
            action=action,
            confidence=None,
            rationale="Released by a moderator." if release else "Blocked by a moderator.",
            note=body.note,
            notice_text=None if release else "This message was not sent. You can reply to appeal.",
            policy_version=latest.policy_version,
            model_version=None,
            created_at=datetime.now(UTC),
        )
        self._events[message_id].append(event)
        # Append-only: the event stays in the trail; only the queue (a
        # view of what still needs a human) loses the row.
        del self._items[message_id]
        return ModerationReviewOut(
            event=event,
            message_status="sent" if release else "blocked",
            notice_sent=True,
        )


_DEMO_MODERATOR_ID = uuid.UUID("00000000-0000-4000-8000-00000000d001")


class GatewayQueueSource:
    """The real backend, against the routes proposed in PR #81.

    Not covered by tests -- see this module's docstring. The shapes come
    from contracts/chat/moderation.py, so if the gateway lands a different
    shape this fails at validation with a readable error rather than
    rendering nonsense.
    """

    def __init__(self, base_url: str, token: str, timeout_s: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout_s

    def fetch_queue(self) -> ModerationQueueOut:
        r = httpx.get(
            f"{self._base}/moderation/queue", headers=self._headers, timeout=self._timeout
        )
        r.raise_for_status()
        return ModerationQueueOut.model_validate(r.json())

    def fetch_events(self, message_id: uuid.UUID) -> ModerationEventsOut:
        r = httpx.get(
            f"{self._base}/moderation/messages/{message_id}/events",
            headers=self._headers,
            timeout=self._timeout,
        )
        r.raise_for_status()
        return ModerationEventsOut.model_validate(r.json())

    def review(
        self, message_id: uuid.UUID, *, release: bool, body: ModerationReviewIn
    ) -> ModerationReviewOut:
        verb = "release" if release else "block"
        r = httpx.post(
            f"{self._base}/moderation/messages/{message_id}/{verb}",
            headers=self._headers,
            json=body.model_dump(mode="json"),
            timeout=self._timeout,
        )
        if r.status_code == 409:
            raise ReviewConflict(r.text)
        r.raise_for_status()
        return ModerationReviewOut.model_validate(r.json())
