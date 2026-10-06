"""
Where the console gets its queue from.

Two backends behind one protocol:

  FixtureQueueSource  -- reads committed JSON fixtures, mutates in memory.
                         The default. Needs nothing running, which makes it
                         the right thing to build and demo the UI against,
                         and the only way to show a voice note, which the
                         chat mock never queues.

  GatewayQueueSource  -- the real routes: GET /moderation/queue,
                         GET .../events, POST .../release|block. Those exist
                         on the gateway now (PR #90) and, with the same
                         rules, on the chat mock (PR #105, #106), which is
                         what this class is tested against. It can be
                         pointed at either, so a test needs no network and
                         no database.

Pick one with the environment, not by editing code -- see
`build_source_from_env`.

Every payload crossing this boundary is validated against
contracts/chat/moderation.py in both backends. A fixture that drifts from
the contract fails loudly here rather than rendering a broken row, and a
gateway that answers in a different shape fails at validation with a
readable error rather than as nonsense on screen.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import ValidationError

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

# The real endpoint returns 25 by default and 100 at most. Asking for the
# maximum keeps a long queue to few round trips; correctness does not depend
# on it, because the loop below follows `next_cursor` regardless of page size.
PAGE_SIZE = 100
# A guard against a server that keeps handing back a cursor: 50 pages of 100
# is 5,000 held messages, far past anything a volunteer moderator will face.
MAX_PAGES = 50


class ReviewConflict(RuntimeError):
    """Someone else decided this message first, or it is no longer in a
    state this action accepts.

    The real gateway answers 409 for both a stale `expected_event_id` and
    "the message is not in a state that action accepts", so one banner
    covers both. The console must show this rather than retrying: two
    moderators had the same item open, and a second click would otherwise
    silently overwrite a decision they never saw.
    """


class SourceError(RuntimeError):
    """The source could not do what was asked, for a reason that is not a
    review conflict: not signed in, not a moderator, gateway down, timed
    out, a 5xx, or an answer in the wrong shape.

    The message is written to be shown to a moderator as-is. Without this,
    each of those would be an unhandled exception inside a Reflex event
    handler -- the moderator clicks Release and nothing happens, with no
    word about why.
    """


class ConsoleConfigError(RuntimeError):
    """The environment asked for a source that cannot be built. Raised at
    startup with the variable named, rather than failing on first click."""


class QueueSource(Protocol):
    label: str

    def fetch_queue(self) -> ModerationQueueOut: ...

    def fetch_events(self, message_id: uuid.UUID) -> ModerationEventsOut: ...

    def review(
        self, message_id: uuid.UUID, *, release: bool, body: ModerationReviewIn
    ) -> ModerationReviewOut: ...


class FixtureQueueSource:
    """Committed fixtures, mutated in memory. Nothing persists across a
    restart -- deliberately: this is for building the UI against, not a
    stand-in datastore.

    `notices_delivered` defaults to **False** because that is what the real
    gateway does today (issue #65: nothing yet carries a notice to the
    sender, so `notice_sent` is always false there). An earlier version of
    this fixture returned True and told the blocked sender "you can reply to
    appeal" -- two things the product does not do yet (appeals are Week 9).
    Fine for a first look at the UI, but a fixture people copy behaviour
    from should not promise what the system does not deliver. Pass True to
    see the happy path.
    """

    def __init__(
        self, fixtures_dir: Path | None = None, *, notices_delivered: bool = False
    ) -> None:
        self.label = "Fixtures — not connected to a gateway"
        self._notices_delivered = notices_delivered
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
        # `notice_text` is "what the sender was actually told" (see the
        # contract), so it is only recorded when a notice really went out.
        notice = None
        if self._notices_delivered and not release:
            notice = "This message was not sent."
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
            notice_text=notice,
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
            notice_sent=self._notices_delivered,
        )


_DEMO_MODERATOR_ID = uuid.UUID("00000000-0000-4000-8000-00000000d001")


class GatewayQueueSource:
    """The real routes, over HTTP. Works against the gateway and against the
    chat mock (`contracts/chat/mock/`), which serves the same four.

    `headers` carries anything extra the target needs. The mock wants
    `X-Mock-Role: moderator`; the real gateway checks the *database role* of
    the user behind the bearer token and needs nothing extra.

    `client` lets a test hand in an in-process client (a FastAPI TestClient
    is an httpx.Client), so the tests exercise the real request/response
    path with no socket and no server. An injected client carries its own
    base URL and timeout -- `base_url` and `timeout_s` here apply only when
    this class makes the requests itself.

    Reaching the real gateway: `/moderation*` is deliberately NOT routed
    through Caddy. The identity behind it is still the stub in legacy auth
    mode -- any UUID bearer *is* that user -- so a routed moderation surface
    would let anyone holding a moderator's UUID release and block. Until
    signed tokens are required for the moderator role (issue #32), the
    console must reach the gateway directly, inside the network.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
        page_size: int = PAGE_SIZE,
    ) -> None:
        self.label = f"Gateway — {base_url}"
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        self._timeout = timeout_s
        self._client = client
        self._page_size = page_size

    # -- transport ---------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One request, with every failure that is not a review conflict
        turned into a SourceError carrying words a moderator can act on."""
        # An injected client owns its own base URL and timeout (the caller
        # built it); only the module-level path needs both spelled out here.
        if self._client is not None:
            client, url, extra = self._client, path, {}
        else:
            client, url, extra = httpx, f"{self._base}{path}", {"timeout": self._timeout}
        try:
            response = client.request(method, url, headers=self._headers, **extra, **kwargs)
        except httpx.TimeoutException as exc:
            raise SourceError(
                f"The gateway did not answer within {self._timeout:g} s. "
                "Nothing was changed; try again."
            ) from exc
        except httpx.TransportError as exc:
            raise SourceError(
                "Could not reach the gateway. Check the console's gateway address "
                f"and that you are on the network. ({type(exc).__name__})"
            ) from exc

        if response.status_code == 409 and method == "POST":
            raise ReviewConflict(_detail(response))
        if response.status_code == 401:
            raise SourceError(
                "The gateway did not accept this console's token (401). It may be wrong or expired."
            )
        if response.status_code == 403:
            raise SourceError(
                "This account is not a moderator on that gateway (403), so it cannot "
                "see or decide held messages."
            )
        if response.status_code == 404:
            raise SourceError(
                f"The gateway says that does not exist (404): {_detail(response)}. It may "
                "have been removed since the queue loaded."
            )
        if response.status_code >= 400:
            raise SourceError(
                f"The gateway returned an error ({response.status_code}): {_detail(response)}"
            )
        return response

    @staticmethod
    def _parse(model, response: httpx.Response):
        try:
            return model.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise SourceError(
                f"The gateway answered in a shape this console does not understand "
                f"({model.__name__}). It may be running a different contract version."
            ) from exc

    # -- the protocol ------------------------------------------------------

    def fetch_queue(self) -> ModerationQueueOut:
        """Every held message, following `next_cursor` to the end.

        The first version made one request and dropped the cursor. The real
        endpoint returns 25 by default, so with more than 25 waiting a
        moderator would clear the first page, see an empty queue, and stop
        -- with more still waiting and nothing on screen to say so. That is
        silent data loss in the one place the product promises a human sees
        every hold.
        """
        items: list[ModerationQueueItem] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params: dict[str, str | int] = {"limit": self._page_size}
            if cursor is not None:
                params["cursor"] = cursor
            page = self._parse(
                ModerationQueueOut, self._send("GET", "/moderation/queue", params=params)
            )
            items.extend(page.items)
            if page.next_cursor is None:
                return ModerationQueueOut(items=items)
            if page.next_cursor in seen:
                raise SourceError(
                    "The gateway returned the same page cursor twice while loading the "
                    "queue; stopping rather than looping. The list shown would be incomplete."
                )
            seen.add(page.next_cursor)
            cursor = page.next_cursor
        raise SourceError(
            f"The queue is longer than {MAX_PAGES * self._page_size} held messages, which is "
            "far past what this console is meant to show. Something upstream is wrong."
        )

    def fetch_events(self, message_id: uuid.UUID) -> ModerationEventsOut:
        return self._parse(
            ModerationEventsOut,
            self._send("GET", f"/moderation/messages/{message_id}/events"),
        )

    def review(
        self, message_id: uuid.UUID, *, release: bool, body: ModerationReviewIn
    ) -> ModerationReviewOut:
        verb = "release" if release else "block"
        return self._parse(
            ModerationReviewOut,
            self._send(
                "POST",
                f"/moderation/messages/{message_id}/{verb}",
                json=body.model_dump(mode="json"),
            ),
        )


def _detail(response: httpx.Response) -> str:
    """The gateway's own `detail`, when it sent one; otherwise the status."""
    try:
        detail = response.json().get("detail")
        if detail:
            return str(detail)
    except (ValueError, AttributeError):
        pass
    return response.reason_phrase or f"HTTP {response.status_code}"


def build_source_from_env(env: dict[str, str] | None = None) -> QueueSource:
    """Choose the backend from the environment, not from the code.

      CONSOLE_SOURCE           fixture (default) | gateway
      CONSOLE_GATEWAY_URL      e.g. http://gateway:8000  (gateway only)
      CONSOLE_GATEWAY_TOKEN    the Bearer token          (gateway only)
      CONSOLE_GATEWAY_HEADERS  optional JSON object of extra headers; the
                               chat mock wants {"X-Mock-Role": "moderator"}

    The previous README said swapping to the gateway was "a config change".
    It was not: the source was constructed at import time and nothing read
    any setting. This is what makes that sentence true.

    Fails at startup naming the variable, rather than on the first click.
    The token is never echoed, in an error or in the UI.
    """
    env = os.environ if env is None else env
    kind = (env.get("CONSOLE_SOURCE") or "fixture").strip().lower()
    if kind == "fixture":
        return FixtureQueueSource()
    if kind != "gateway":
        raise ConsoleConfigError(f"CONSOLE_SOURCE={kind!r} is not 'fixture' or 'gateway'")

    url = (env.get("CONSOLE_GATEWAY_URL") or "").strip()
    token = (env.get("CONSOLE_GATEWAY_TOKEN") or "").strip()
    if not url:
        raise ConsoleConfigError("CONSOLE_SOURCE=gateway needs CONSOLE_GATEWAY_URL")
    if not token:
        raise ConsoleConfigError("CONSOLE_SOURCE=gateway needs CONSOLE_GATEWAY_TOKEN")

    headers: dict[str, str] | None = None
    raw_headers = (env.get("CONSOLE_GATEWAY_HEADERS") or "").strip()
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
        except json.JSONDecodeError as exc:
            raise ConsoleConfigError("CONSOLE_GATEWAY_HEADERS is not valid JSON") from exc
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ConsoleConfigError(
                "CONSOLE_GATEWAY_HEADERS must be a JSON object of string keys and values"
            )
        headers = parsed
    return GatewayQueueSource(url, token, headers=headers)
