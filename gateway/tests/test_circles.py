"""Gateway circle routes, tested against a fake backbone.

These deliberately do NOT touch Postgres or the real backbone. The point
of the interface is that the gateway can be exercised against any
CircleBackbone; a fake one is the cheapest proof that's actually true. If
these tests ever need a database to pass, the boundary has leaked.

Delivery semantics (offline queueing, ordering, crash safety) are the
backbone's job and are tested there --
backbone/spike-custom-lite/tests/test_circles.py.
"""

from datetime import datetime, timezone
from typing import List, Optional

import pytest
from starlette.testclient import TestClient

import circles
from backbone_client import HttpCircleBackbone
from interfaces import BackboneUnavailable, CircleBackbone, CircleMessage
from main import app


class FakeBackbone(CircleBackbone):
    """In-memory CircleBackbone. Subclasses the real ABC, so it can't
    silently drift out of conformance either."""

    def __init__(self):
        self.circles = {}
        self.members = {}
        self.messages = {}
        self._next_id = 1

    async def create_circle(self, name: str) -> str:
        circle_id = str(self._next_id)
        self._next_id += 1
        self.circles[circle_id] = name
        self.members[circle_id] = []
        self.messages[circle_id] = []
        return circle_id

    async def add_member(self, circle_id: str, user_id: str) -> None:
        if user_id not in self.members[circle_id]:
            self.members[circle_id].append(user_id)

    async def remove_member(self, circle_id: str, user_id: str) -> None:
        if user_id in self.members[circle_id]:
            self.members[circle_id].remove(user_id)

    async def list_members(self, circle_id: str) -> List[str]:
        return list(self.members[circle_id])

    async def post_announcement(self, circle_id: str, sender_id: str, body: str) -> str:
        message_id = str(self._next_id)
        self._next_id += 1
        self.messages[circle_id].append(
            CircleMessage(
                id=message_id,
                circle_id=circle_id,
                sender_id=sender_id,
                body=body,
                created_at=datetime.now(timezone.utc),
            )
        )
        return message_id

    async def list_messages(
        self, circle_id: str, limit: int = 50, before: Optional[str] = None
    ) -> List[CircleMessage]:
        return list(reversed(self.messages[circle_id]))[:limit]


class UnavailableBackbone(CircleBackbone):
    """Every call fails the way a down backbone does."""

    async def create_circle(self, name):
        raise BackboneUnavailable("connection refused")

    async def add_member(self, circle_id, user_id):
        raise BackboneUnavailable("connection refused")

    async def remove_member(self, circle_id, user_id):
        raise BackboneUnavailable("connection refused")

    async def list_members(self, circle_id):
        raise BackboneUnavailable("connection refused")

    async def post_announcement(self, circle_id, sender_id, body):
        raise BackboneUnavailable("connection refused")

    async def list_messages(self, circle_id, limit=50, before=None):
        raise BackboneUnavailable("connection refused")


@pytest.fixture
def client():
    circles.set_backbone(FakeBackbone())
    with TestClient(app) as c:
        yield c
    circles.set_backbone(HttpCircleBackbone())  # restore module state


def test_http_client_satisfies_the_contract():
    """The real client implements every contract method.

    Python enforces this at instantiation because HttpCircleBackbone
    subclasses the ABC -- instantiating with a method missing raises
    TypeError. Asserted explicitly so a future contract addition fails
    here, loudly, rather than at container startup.
    """
    backbone = HttpCircleBackbone(base_url="http://example.invalid")
    assert isinstance(backbone, CircleBackbone)

    for name in (
        "create_circle",
        "add_member",
        "remove_member",
        "list_members",
        "post_announcement",
        "list_messages",
    ):
        assert callable(getattr(backbone, name)), f"missing contract method: {name}"


def test_create_circle_and_manage_members(client):
    circle_id = client.post("/circles", json={"name": "Sunday Satsang"}).json()["circle_id"]

    for user in ("bob", "carol"):
        assert client.post(
            f"/circles/{circle_id}/members", json={"user_id": user}
        ).status_code == 200

    assert client.get(f"/circles/{circle_id}/members").json()["members"] == ["bob", "carol"]

    assert client.delete(f"/circles/{circle_id}/members/bob").status_code == 200
    assert client.get(f"/circles/{circle_id}/members").json()["members"] == ["carol"]


def test_announce_and_read_history(client):
    circle_id = client.post("/circles", json={"name": "Announcements"}).json()["circle_id"]
    client.post(f"/circles/{circle_id}/members", json={"user_id": "bob"})

    resp = client.post(
        f"/circles/{circle_id}/announce",
        json={"sender_id": "alice", "body": "satsang at 6pm"},
    )
    assert resp.status_code == 200
    assert resp.json()["message_id"]

    messages = client.get(f"/circles/{circle_id}/messages").json()["messages"]
    assert len(messages) == 1
    assert messages[0]["body"] == "satsang at 6pm"
    assert messages[0]["sender_id"] == "alice"


def test_backbone_down_returns_503_not_500():
    """A dead backbone is not a gateway bug -- same distinction /db-check
    already makes for Postgres."""
    circles.set_backbone(UnavailableBackbone())
    try:
        with TestClient(app) as c:
            assert c.post("/circles", json={"name": "x"}).status_code == 503
            assert c.get("/circles/1/members").status_code == 503
            assert c.post(
                "/circles/1/announce", json={"sender_id": "a", "body": "b"}
            ).status_code == 503
    finally:
        circles.set_backbone(HttpCircleBackbone())


def test_existing_health_endpoint_still_works(client):
    """Week 1's contract is not disturbed by any of this."""
    assert client.get("/health").json() == {"status": "ok"}
